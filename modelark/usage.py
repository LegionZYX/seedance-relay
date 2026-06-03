"""
ModelArk 统一 token usage 提取器
==================================
不同 API 的 usage 字段位置不一样，这里归一化成同一 schema：

    Usage(
        prompt_tokens,           # 输入 token
        completion_tokens,       # 输出 token
        reasoning_tokens,        # R1/seed-1.6 思考 token（计费中通常归入 output）
        cached_tokens,           # context cache 命中（折扣价）
        image_tokens,            # 图片输入折算 token (embedding-vision)
        generated_images,        # 图片生成接口出图数
        total_tokens,
        raw,                     # 原始 dict 备查
    )

支持的 API 形态：
  - chat        OpenAI 兼容: response["usage"]
  - responses   Responses API: response["usage"] 字段名 input_tokens/output_tokens
  - image       Image generation: response["usage"] 含 generated_images
  - video_task  Video task retrieve: task["usage"] (异步任务)
  - embedding   Embedding API: response["usage"]
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0       # 包含在 completion_tokens 内
    cached_tokens: int = 0          # 包含在 prompt_tokens 内 (走缓存价)
    image_tokens: int = 0           # 多模态/embedding-vision
    generated_images: int = 0
    total_tokens: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    # ---- 派生 ----
    @property
    def billable_input(self) -> int:
        """走标准价的 input token = prompt_tokens - cached_tokens"""
        return max(0, self.prompt_tokens - self.cached_tokens)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["billable_input"] = self.billable_input
        d.pop("raw", None)
        return d


# =====================================================================
# Adapters: 各种 API 形态 -> Usage
# =====================================================================
def _chat_usage(usage: Dict[str, Any]) -> Usage:
    """Chat / Responses API 的 OpenAI 兼容结构。
    Responses API 用 input_tokens / output_tokens, Chat 用 prompt_tokens / completion_tokens。
    都兼容。"""
    if not usage:
        return Usage()

    prompt = usage.get("prompt_tokens",
              usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens",
                  usage.get("output_tokens", 0)) or 0

    # 思考模型 (R1, seed-1.6 thinking)
    ctd = usage.get("completion_tokens_details", {}) or {}
    reasoning = ctd.get("reasoning_tokens", 0) or 0

    # context cache 命中
    ptd = usage.get("prompt_tokens_details",
            usage.get("input_tokens_details", {})) or {}
    cached = ptd.get("cached_tokens", 0) or 0

    # 多模态输入图片折算
    image = ptd.get("image_tokens", 0) or 0

    total = usage.get("total_tokens", prompt + completion) or 0

    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        cached_tokens=cached,
        image_tokens=image,
        total_tokens=total,
        raw=usage,
    )


def _image_usage(usage: Dict[str, Any]) -> Usage:
    """Image generation API: usage 里通常是 {output_tokens, generated_images}。"""
    if not usage:
        return Usage()
    return Usage(
        prompt_tokens=usage.get("input_tokens", 0) or 0,
        completion_tokens=usage.get("output_tokens", 0) or 0,
        generated_images=usage.get("generated_images",
                          usage.get("images", 0)) or 0,
        total_tokens=usage.get("total_tokens", 0) or 0,
        raw=usage,
    )


def _embedding_usage(usage: Dict[str, Any]) -> Usage:
    """Embedding API: {prompt_tokens, total_tokens}。
    多模态 embedding 含 image/video token 拆分。"""
    if not usage:
        return Usage()
    prompt = usage.get("prompt_tokens", 0) or 0
    image  = usage.get("image_tokens", 0) or 0
    total  = usage.get("total_tokens", prompt) or 0
    return Usage(
        prompt_tokens=prompt,
        image_tokens=image,
        total_tokens=total,
        raw=usage,
    )


def _video_task_usage(task: Dict[str, Any]) -> Usage:
    """Video generation: 整个 task dict, usage 在根级，仅 succeeded 任务才有。"""
    usage = task.get("usage") or {}
    if not usage:
        return Usage()
    completion = usage.get("completion_tokens", 0) or 0
    total = usage.get("total_tokens", completion) or 0
    return Usage(
        completion_tokens=completion,
        total_tokens=total,
        raw=usage,
    )


# =====================================================================
# 主入口: 自动识别响应类型
# =====================================================================
def extract_usage(response: Dict[str, Any],
                  api_type: Optional[str] = None) -> Usage:
    """
    从任意 ModelArk API 响应中提取 usage。

    api_type 可显式指定，否则按响应结构自动推断:
      - "chat" / "responses"   -> _chat_usage
      - "image"                -> _image_usage
      - "embedding"            -> _embedding_usage
      - "video_task"           -> _video_task_usage
    """
    if api_type:
        if api_type == "video_task":
            # 视频任务: 传整个 task dict, 提取器自己拿 .usage
            return _video_task_usage(response)
        return {
            "chat":       _chat_usage,
            "responses":  _chat_usage,
            "image":      _image_usage,
            "embedding":  _embedding_usage,
        }[api_type](response.get("usage", response))

    if not isinstance(response, dict):
        return Usage()

    # 视频任务: 顶层有 status + content.video_url
    if "status" in response and "content" in response and \
       isinstance(response.get("content"), dict) and \
       "video_url" in response["content"]:
        return _video_task_usage(response)

    usage = response.get("usage") or {}
    if not usage:
        return Usage(raw={})

    # Image: 有 generated_images 字段
    if "generated_images" in usage or "images" in usage:
        return _image_usage(usage)

    # Embedding: 有 data 数组里是 vector
    data = response.get("data")
    if isinstance(data, list) and data and \
       isinstance(data[0], dict) and "embedding" in data[0]:
        return _embedding_usage(usage)

    # Default: chat / responses
    return _chat_usage(usage)


# =====================================================================
# Tracker: 多次调用的累计 + 美元换算
# =====================================================================
@dataclass
class UsageRecord:
    label: str
    model: str
    api_type: str
    usage: Usage
    cost_usd: float = 0.0
    service_tier: str = "default"   # "default" | "flex"


# flex 服务等级官方计价为 default 的 50%
FLEX_DISCOUNT = 0.5


class UsageTracker:
    """累加多次调用的 usage 并计算 USD。"""

    def __init__(self, pricing=None):
        # 延迟导入避免循环
        from .pricing import PRICING
        self.pricing = pricing or PRICING
        self.records: List[UsageRecord] = []

    def track(self, label: str, model: str, response: Any,
              api_type: Optional[str] = None,
              service_tier: Optional[str] = None,
              has_video_ref: bool = False) -> UsageRecord:
        """记一笔调用。

        视频任务 (api_type='video_task') 走"分辨率 × 是否含视频参考"分档定价：
          - resolution 自动从 task dict 的 'resolution' 字段读
          - has_video_ref 由调用方告知（输入 content[] 是否含 video_url）
        """
        usage = extract_usage(response, api_type)
        api_type = api_type or self._guess_type(response)
        # 视频任务的 service_tier / resolution 直接出现在 task 字典里
        resolution = None
        if isinstance(response, dict):
            if service_tier is None:
                service_tier = response.get("service_tier", "default")
            resolution = response.get("resolution")
        service_tier = service_tier or "default"
        cost = self._compute_cost(model, api_type, usage, service_tier,
                                  resolution=resolution,
                                  has_video_ref=has_video_ref)
        rec = UsageRecord(label=label, model=model, api_type=api_type,
                          usage=usage, cost_usd=cost, service_tier=service_tier)
        self.records.append(rec)
        return rec

    @staticmethod
    def _guess_type(response: Any) -> str:
        if isinstance(response, dict):
            if "status" in response and isinstance(
                    response.get("content"), dict) and \
                    "video_url" in response["content"]:
                return "video_task"
            if isinstance(response.get("data"), list) and \
                    response["data"] and \
                    "embedding" in response["data"][0]:
                return "embedding"
            u = response.get("usage", {}) or {}
            if "generated_images" in u or "images" in u:
                return "image"
        return "chat"

    def _compute_cost(self, model: str, api_type: str,
                      usage: Usage, service_tier: str = "default",
                      resolution: Optional[str] = None,
                      has_video_ref: bool = False) -> float:
        # 模糊匹配 model id（含日期后缀也能命中）
        from .pricing import get_price, get_output_rate
        price = self.pricing.get(model) or get_price(model)
        if not price:
            return 0.0  # 没价格表就先记 0，之后可以补

        # 按 API 类型走不同公式
        if api_type == "video_task":
            # 视频按 completion_tokens 单价；若价表里有 output_by_context 则按
            # (resolution, has_video_ref) 选档
            rate = get_output_rate(model, resolution, has_video_ref)
            cost = usage.completion_tokens / 1000 * rate
        elif api_type == "image":
            # 图片生成: 按出图数 OR 按 token，取看哪种价格表给了什么
            if "per_image" in price:
                cost = usage.generated_images * price["per_image"]
            else:
                cost = usage.completion_tokens / 1000 * price.get("output", 0)
        elif api_type == "embedding":
            cost = usage.total_tokens / 1000 * price.get("input", 0)
        else:
            # chat / responses: input + output, 加上缓存折扣
            billable_in = usage.billable_input
            cached_in   = usage.cached_tokens
            cost  = billable_in / 1000 * price.get("input", 0)
            cost += cached_in   / 1000 * price.get("cached_input",
                                                    price.get("input", 0) * 0.1)
            cost += usage.completion_tokens / 1000 * price.get("output", 0)

        # flex 服务等级 = 默认价 × 50%（官方文档明确）
        if service_tier == "flex":
            cost *= FLEX_DISCOUNT
        return cost

    # ---- 输出 ----
    def summary(self) -> Dict[str, Any]:
        total_cost = sum(r.cost_usd for r in self.records)
        return {
            "calls": len(self.records),
            "total_cost_usd": round(total_cost, 6),
            "records": [
                {
                    "label": r.label,
                    "model": r.model,
                    "api_type": r.api_type,
                    "service_tier": r.service_tier,
                    "usage": r.usage.to_dict(),
                    "cost_usd": round(r.cost_usd, 6),
                }
                for r in self.records
            ],
        }

    def print_table(self):
        if not self.records:
            print("(no usage recorded)")
            return
        print()
        print(f"{'#':<3} {'label':<14} {'api':<11} {'tier':<7} {'model':<32} "
              f"{'in':>8} {'out':>8} {'cached':>7} {'imgs':>5} "
              f"{'total':>9} {'USD':>10}")
        print("-" * 125)
        gt = 0
        gc = 0.0
        for i, r in enumerate(self.records, 1):
            u = r.usage
            print(f"{i:<3} {r.label:<14} {r.api_type:<11} {r.service_tier:<7} "
                  f"{r.model[:32]:<32} "
                  f"{u.prompt_tokens:>8} {u.completion_tokens:>8} "
                  f"{u.cached_tokens:>7} {u.generated_images:>5} "
                  f"{u.total_tokens:>9} {r.cost_usd:>10.6f}")
            gt += u.total_tokens
            gc += r.cost_usd
        print("-" * 125)
        print(f"{'':>84} TOTAL: {gt:>9} tokens   ${gc:.6f}")
