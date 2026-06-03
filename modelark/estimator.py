"""
ModelArk 成本预估器 + 中转站余额预扣助手
========================================
中转站典型流程（防止用户余额不足撑爆你账号）：

    est = estimate_video_cost(model, resolution="1080p", duration=5)
    if user.balance_usd < est.max_cost_usd:
        return reject("余额不足")
    rsv = Reservation.hold(user_id, est)        # 冻结 max_cost
    user.balance_usd -= rsv.held_usd
    # ... 调 BytePlus 创建 + 轮询 ...
    if task.status == "succeeded":
        actual_usd = compute_actual_cost(task)
        refund = rsv.settle(actual_usd)         # 退差额
        user.balance_usd += refund
    else:
        user.balance_usd += rsv.refund()        # 全退

token 估算依据（来自 BytePlus 官方文档示例）：
  - Seedance 2.0 1080p 16:9 5s @24fps = 108_900 tokens
  - Seedance 1.5 Pro 720p 16:9 5s @24fps = 82_280 tokens
推算公式：tokens ≈ W × H × duration × fps × rate(model)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Tuple


# =====================================================================
# 1) 分辨率查找表（按官方文档列出的 W×H）
# =====================================================================
# 老 Seedance 1.0 系列
RES_TABLE_V1: Dict[str, Dict[str, Tuple[int, int]]] = {
    "480p":  {"16:9": (864, 480), "4:3": (736, 544), "1:1": (640, 640),
              "3:4": (544, 736),  "9:16": (480, 864), "21:9": (960, 416)},
    "720p":  {"16:9": (1248, 704),"4:3": (1120, 832),"1:1": (960, 960),
              "3:4": (832, 1120), "9:16": (704, 1248),"21:9": (1504, 640)},
    "1080p": {"16:9": (1920,1088),"4:3": (1664,1248),"1:1": (1440,1440),
              "3:4": (1248,1664), "9:16": (1088,1920),"21:9": (2176, 928)},
}
# Seedance 1.5 Pro 与 Seedance 2.0 系列
RES_TABLE_V2: Dict[str, Dict[str, Tuple[int, int]]] = {
    "480p":  {"16:9": (864, 496), "4:3": (752, 560), "1:1": (640, 640),
              "3:4": (560, 752),  "9:16": (496, 864), "21:9": (992, 432)},
    "720p":  {"16:9": (1280, 720),"4:3": (1112, 834),"1:1": (960, 960),
              "3:4": (834, 1112), "9:16": (720, 1280),"21:9": (1470, 630)},
    "1080p": {"16:9": (1920,1080),"4:3": (1664,1248),"1:1": (1440,1440),
              "3:4": (1248,1664), "9:16": (1080,1920),"21:9": (2206, 946)},
}

# 哪些模型走 V2 表（其余走 V1）
V2_MODELS = {
    "seedance-1-5-pro",
    "dreamina-seedance-2-0",
    "dreamina-seedance-2-0-fast",
}


def _resolve_dims(model: str, resolution: str, ratio: str) -> Tuple[int, int]:
    base = _strip_date(model)
    table = RES_TABLE_V2 if base in V2_MODELS else RES_TABLE_V1
    res = table.get(resolution, table["720p"])
    return res.get(ratio, res["16:9"])


def _strip_date(model: str) -> str:
    """seedance-1-5-pro-251215 -> seedance-1-5-pro"""
    parts = model.rsplit("-", 1)
    return parts[0] if len(parts) == 2 and parts[1].isdigit() else model


# =====================================================================
# 2) 每像素·每秒生成多少 token（官方示例反推）
#    保守原则：不在表里的模型按"已知最大值 × 1.2"兜底
#
#    注意：实测数据显示 token 数对像素是**亚线性**关系（同样的时长，
#    480p 的 tokens-per-pixel 比 1080p 高约 2 倍）。所以下表是个粗估，
#    更精确的覆盖见 TOKENS_PER_SEC_OVERRIDE。
# =====================================================================
TOKENS_PER_PX_SEC: Dict[str, float] = {
    # 已知数据点（按 1080p 计算的 px·sec 速率，对低分辨率会低估）
    "dreamina-seedance-2-0":      0.01051,    # 1080p×5s = 108_900
    "dreamina-seedance-2-0-fast": 0.01051,
    "seedance-1-5-pro":           0.01786,    # 720p×5s = 82_280
    "seedance-1-0-pro":           0.01786,
    "seedance-1-0-pro-fast":      0.01786,
    "seedance-1-0-lite-t2v":      0.01786,
    "seedance-1-0-lite-i2v":      0.01786,
}
FALLBACK_RATE = max(TOKENS_PER_PX_SEC.values()) * 1.2  # 给未知模型一个上限


# 校准过的"每秒 tokens"（实测数据点 + 像素比例外推）。
# 优先使用，命中后跳过 px·sec 公式。Key = (model_base, resolution)
TOKENS_PER_SEC_OVERRIDE: Dict[str, Dict[str, int]] = {
    "dreamina-seedance-2-0": {
        # 实测: 480p 16:9 5s with audio = 50_638 → 10_128 tokens/s
        "480p":  10_128,
        # 实测 + 文档示例: 720p 16:9 5s with audio = 108_900 → 21_780 tokens/s
        "720p":  21_780,
        # 1080p 没实测，按 720p×(1080p_px/720p_px) 比例外推:
        # 1080p 16:9 = 1920×1080 = 2_073_600;  720p 16:9 = 1280×720 = 921_600
        # 21_780 × 2_073_600 / 921_600 ≈ 49_005 tokens/s（保守上限估算）
        "1080p": 49_005,
    },
    "dreamina-seedance-2-0-fast": {
        # Fast 版本暂无实测，按 Pro 同档（保守上限）
        "480p":  10_128, "720p": 21_780, "1080p": 49_005,
    },
    "seedance-1-5-pro": {
        # 文档示例: 720p 16:9 5s = 82_280 → 16_456 tokens/s
        "720p": 16_456,
        # 480p / 1080p 没数据点，按 SD 2.0 同比例外推
        "480p":  10_128 * 16_456 // 21_780,   # ~7_652
        "1080p": 49_005 * 16_456 // 21_780,   # ~37_027
    },
}


# =====================================================================
# 3) 估算结果数据类
# =====================================================================
@dataclass
class CostEstimate:
    api_type: str               # "video" | "image" | "chat" | "embedding"
    model: str
    service_tier: str = "default"   # default | flex (flex 自动 50%)
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0    # 按"中位数参数"估
    max_cost_usd: float = 0.0          # 已加 buffer，给"预扣"用
    breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =====================================================================
# 4) 视频成本预估
# =====================================================================
def estimate_video_cost(
    model: str,
    *,
    resolution: str = "720p",
    ratio: str = "16:9",
    duration: Optional[int] = 5,
    frames: Optional[int] = None,
    fps: int = 24,
    generate_audio: bool = False,
    has_video_ref: bool = False,
    service_tier: str = "default",
    buffer_pct: float = 0.10,
    pricing: Optional[Dict[str, Dict[str, Any]]] = None,
) -> CostEstimate:
    """估算单个视频生成任务的 token 数与 USD 成本上限。

    参数模糊时取 worst case：
      - duration=-1 (智能选时长) -> 取该模型时长上限 (15s for 2.0/1.5 Pro, 12s 其他)
      - ratio="adaptive" -> 取像素最大的 16:9
      - frames 优先于 duration（以 frames/fps 折算时长）
      - generate_audio=True -> 多 +5% buffer

    has_video_ref:
      输入 content[] 是否包含 type=video_url。SD 2.0 Pro 等模型对"含视频参考"
      与"无视频参考"分别定价，含视频参考通常更便宜（约 60%）。

    返回:
      estimated_cost_usd: 给前端展示用
      max_cost_usd:        给"预扣"用，加了 buffer，是真正的"最贵能要多少钱"
    """
    from .pricing import get_price, get_output_rate
    base = _strip_date(model)

    # 1) 时长 -> 秒
    if frames is not None:
        seconds = frames / max(fps, 1)
    elif duration is not None and duration > 0:
        seconds = duration
    else:  # duration=-1 智能选 -> 取上限
        seconds = 15 if base in V2_MODELS else 12

    # 2) 分辨率 -> W×H（adaptive 取最大像素）
    if ratio == "adaptive":
        ratio = "16:9"
    w, h = _resolve_dims(model, resolution, ratio)

    # 3) tokens 估算: 优先用校准过的 per-second 表 (按 16:9 实测/插值),
    #    其次回退到 px·sec 通用公式。
    override = TOKENS_PER_SEC_OVERRIDE.get(base, {}).get(resolution)
    if override is not None:
        tokens_est = int(math.ceil(override * seconds))
        rate_used = f"override:{override}/s"
    else:
        rate = TOKENS_PER_PX_SEC.get(base, FALLBACK_RATE)
        tokens_est = int(math.ceil(w * h * seconds * rate))
        rate_used = rate

    # 4) 单价 -> USD（按 resolution × has_video_ref 选档；缺失则回落 flat output）
    if pricing and model in pricing:
        # 临时覆盖：只对当前调用生效
        from .pricing import PRICING as _GLOBAL
        bak = _GLOBAL.get(model)
        _GLOBAL[model] = pricing[model]
        try:
            rate_usd = get_output_rate(model, resolution, has_video_ref)
        finally:
            if bak is None:
                _GLOBAL.pop(model, None)
            else:
                _GLOBAL[model] = bak
    else:
        rate_usd = get_output_rate(model, resolution, has_video_ref)
    cost_est = tokens_est / 1000 * rate_usd

    # 5) flex 半价
    if service_tier == "flex":
        cost_est *= 0.5

    # 6) buffer 上限
    buf = buffer_pct + (0.05 if generate_audio else 0.0)
    if base not in TOKENS_PER_PX_SEC:
        buf += 0.20  # 未知模型再加 20% 兜底
    max_cost = cost_est * (1 + buf)

    return CostEstimate(
        api_type="video",
        model=model,
        service_tier=service_tier,
        estimated_tokens=tokens_est,
        estimated_cost_usd=round(cost_est, 6),
        max_cost_usd=round(max_cost, 6),
        breakdown={
            "resolution": resolution, "ratio": ratio,
            "width": w, "height": h, "seconds": round(seconds, 3),
            "fps": fps, "rate_used": rate_used,
            "has_video_ref": has_video_ref,
            "price_per_1k_output_usd": rate_usd,
            "buffer_pct": round(buf, 3),
            "generate_audio": generate_audio,
        },
    )


# =====================================================================
# 5) 图片生成成本预估（per_image × n）
# =====================================================================
def estimate_image_cost(
    model: str,
    n: int = 1,
    *,
    buffer_pct: float = 0.0,
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> CostEstimate:
    from .pricing import get_price
    price = (pricing or {}).get(model) or get_price(model) or {}
    per_img = price.get("per_image", 0.0)
    cost = per_img * max(n, 1)
    return CostEstimate(
        api_type="image",
        model=model,
        estimated_tokens=0,
        estimated_cost_usd=round(cost, 6),
        max_cost_usd=round(cost * (1 + buffer_pct), 6),
        breakdown={"n": n, "per_image_usd": per_img, "buffer_pct": buffer_pct},
    )


# =====================================================================
# 6) Chat / Responses 成本预估
#    input 推荐用 BytePlus 的 /tokenization 离线接口精确算；
#    这里给两种方式：传 prompt_tokens（推荐）或传 prompt 文本走粗估
# =====================================================================
def _rough_token_count(text: str) -> int:
    """粗估：英文按 4 char/token，中文按 1.6 char/token，混合估个均值。"""
    if not text:
        return 0
    n_cn = sum(1 for c in text if "一" <= c <= "鿿")
    n_other = len(text) - n_cn
    return int(math.ceil(n_cn / 1.6 + n_other / 4))


def estimate_chat_cost(
    model: str,
    *,
    prompt_tokens: Optional[int] = None,
    prompt_text: Optional[str] = None,
    max_tokens: int = 1024,
    cached_tokens: int = 0,
    service_tier: str = "default",
    buffer_pct: float = 0.10,
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> CostEstimate:
    """估算单次 chat / responses 调用的成本上限。
       output 取 max_tokens 上限 (worst case)。
       建议在调用前用 /tokenization 算精确 prompt_tokens 传进来。
    """
    from .pricing import get_price
    if prompt_tokens is None:
        prompt_tokens = _rough_token_count(prompt_text or "")

    price = (pricing or {}).get(model) or get_price(model) or {}
    p_in = price.get("input", 0.0)
    p_out = price.get("output", 0.0)
    p_cached = price.get("cached_input", p_in * 0.1)

    billable_in = max(0, prompt_tokens - cached_tokens)
    cost = (billable_in / 1000) * p_in
    cost += (cached_tokens / 1000) * p_cached
    cost += (max_tokens / 1000) * p_out      # output 按上限算

    if service_tier == "flex":
        cost *= 0.5

    return CostEstimate(
        api_type="chat",
        model=model,
        service_tier=service_tier,
        estimated_tokens=prompt_tokens + max_tokens,
        estimated_cost_usd=round(cost, 6),
        max_cost_usd=round(cost * (1 + buffer_pct), 6),
        breakdown={
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "max_output_tokens": max_tokens,
            "price_input": p_in, "price_output": p_out,
            "price_cached": p_cached, "buffer_pct": buffer_pct,
        },
    )


# =====================================================================
# 7) 余额检查 + Reservation（预扣/对账）
# =====================================================================
def can_user_afford(balance_usd: float,
                    estimate: CostEstimate) -> Tuple[bool, str]:
    """中转站标准的"够不够"检查。"""
    need = estimate.max_cost_usd
    if balance_usd >= need:
        return True, "ok"
    return False, (f"balance_insufficient: need ${need:.6f}, "
                   f"have ${balance_usd:.6f}, "
                   f"short ${need - balance_usd:.6f}")


@dataclass
class Reservation:
    """中转站预扣 / 结算 / 退款的状态对象。
    用法（不依赖你的 ORM, 你只要把 held_usd / final_usd 同步到自己 DB 即可）：

        rsv = Reservation.hold(user_id="u1", estimate=est)
        # 这一刻：从用户余额里扣 rsv.held_usd 进"冻结"账户

        # 提交 BytePlus，等任务终态
        if status == "succeeded":
            refund = rsv.settle(actual_usd)     # 退多扣的部分
            user.balance += refund
        else:
            user.balance += rsv.refund()        # 全退

        # 最后再把 rsv 持久化到对账表
    """
    user_id: str
    estimate: CostEstimate
    held_usd: float                 # 实际冻结的金额 = max_cost_usd
    state: str = "held"             # "held" | "settled" | "refunded"
    final_usd: float = 0.0          # 实际扣费金额（settle 后才有）
    task_id: str = ""
    note: str = ""

    @classmethod
    def hold(cls, user_id: str, estimate: CostEstimate,
             task_id: str = "") -> "Reservation":
        return cls(user_id=user_id, estimate=estimate,
                   held_usd=estimate.max_cost_usd, task_id=task_id)

    def settle(self, actual_usd: float) -> float:
        """succeeded：用 actual 替代冻结额，返回应退金额。"""
        if self.state != "held":
            raise RuntimeError(f"reservation already {self.state}")
        actual_usd = max(0.0, float(actual_usd))
        # 防御：actual 比预扣还多（极小概率），按 actual 真扣
        if actual_usd > self.held_usd:
            self.note = (f"⚠️  actual ${actual_usd:.6f} > held "
                         f"${self.held_usd:.6f}, 真实扣 actual")
            self.final_usd = actual_usd
            refund = 0.0
        else:
            self.final_usd = actual_usd
            refund = self.held_usd - actual_usd
        self.state = "settled"
        return round(refund, 6)

    def refund(self) -> float:
        """failed/cancelled/expired：全退。"""
        if self.state != "held":
            raise RuntimeError(f"reservation already {self.state}")
        self.state = "refunded"
        self.final_usd = 0.0
        return self.held_usd

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["estimate"] = self.estimate.to_dict()
        return d


# =====================================================================
# 8) 从已完成的 video task 拿"实际成本"
#    （供 settle() 使用；和 UsageTracker 一致的口径）
# =====================================================================
def actual_video_cost(task: Dict[str, Any],
                      *,
                      has_video_ref: bool = False,
                      pricing: Optional[Dict[str, Dict[str, Any]]] = None
                      ) -> float:
    """succeeded video task -> USD 真实成本。

    自动按 (resolution, has_video_ref) 选档单价；retrieve 响应里有 resolution
    字段，但是否含视频参考要由调用方告知（来自原始请求 content[]）。
    """
    from .pricing import get_output_rate
    if not isinstance(task, dict) or task.get("status") != "succeeded":
        return 0.0
    usage = task.get("usage") or {}
    completion = usage.get("completion_tokens", 0) or 0
    model = task.get("model", "")
    resolution = task.get("resolution")

    if pricing and model in pricing:
        from .pricing import PRICING as _GLOBAL
        bak = _GLOBAL.get(model)
        _GLOBAL[model] = pricing[model]
        try:
            rate = get_output_rate(model, resolution, has_video_ref)
        finally:
            if bak is None:
                _GLOBAL.pop(model, None)
            else:
                _GLOBAL[model] = bak
    else:
        rate = get_output_rate(model, resolution, has_video_ref)

    cost = completion / 1000 * rate
    if task.get("service_tier") == "flex":
        cost *= 0.5
    return round(cost, 6)


def has_video_reference(content: list) -> bool:
    """从 create-task 的 content[] 判断是否包含视频参考。"""
    if not content:
        return False
    return any(isinstance(b, dict) and b.get("type") == "video_url"
               for b in content)
