"""
ModelArk 模型定价表（USD per 1K tokens, 除非标注 per_image）
=============================================================
正式对账请：
  1) 以 https://docs.byteplus.com/en/docs/ModelArk/1544106 为准
  2) 通过 load_pricing_from_json("my_prices.json") 覆盖

数据结构：
  {
    "model_id": {
      "input":         <USD / 1K input tokens>,
      "cached_input":  <USD / 1K cached tokens, 可选, 默认 = input * 0.1>,
      "output":        <USD / 1K output tokens>,    ← flat 单价（fallback）
      "per_image":     <USD per image, 仅图片生成且按图计费时有>,

      # 仅视频模型支持的"分辨率 × 是否含视频参考"分档定价（可选）
      "output_by_context": {
        "no_video_ref":   {"480p": ..., "720p": ..., "1080p": ...},
        "with_video_ref": {"480p": ..., "720p": ..., "1080p": ...},
      },
    }
  }

视频任务挑哪一档单价：
  - 输入 content[] 里有 type=video_url -> with_video_ref
  - 否则                                 -> no_video_ref
  - 缺失字段则回落到 flat output
"""

import json
import os
from typing import Any, Dict, Optional


# 单位: USD per 1K tokens
PRICING: Dict[str, Dict[str, Any]] = {

    # =========== Text generation ===========
    "seed-1-6-250615":         {"input": 0.0006, "output": 0.0024},
    "seed-1-6-flash-250615":   {"input": 0.00015, "output": 0.0006},
    "seed-1-8-251228":         {"input": 0.0006, "output": 0.0024},
    "seed-2-0-lite-260228":    {"input": 0.00015, "output": 0.0006},
    "seed-translation":        {"input": 0.0003, "output": 0.0009},

    # =========== DeepSeek ===========
    "deepseek-v3":             {"input": 0.00027, "output": 0.0011},
    "deepseek-v3-1":           {"input": 0.00027, "output": 0.0011},
    "deepseek-v3-2":           {"input": 0.00027, "output": 0.0011},
    "deepseek-r1-250528":      {"input": 0.00055, "output": 0.00219},
    "deepseek-r1":             {"input": 0.00055, "output": 0.00219},

    # =========== 第三方开源 ===========
    "kimi-k2":                 {"input": 0.00057, "output": 0.0023},
    "gpt-oss-120b":            {"input": 0.0001,  "output": 0.0005},

    # =========== 老 Skylark ===========
    "skylark-pro":             {"input": 0.0006, "output": 0.0024},
    "skylark-vision":          {"input": 0.0006, "output": 0.0024},

    # =========== 图片生成 (按出图数计费) ===========
    "seedream-3-0-t2i-250415":     {"per_image": 0.03},
    "seedream-4-0-250716":         {"per_image": 0.03},
    "seededit-3-0-i2i-250628":     {"per_image": 0.03},

    # =========== 视频生成 ===========
    # ---- Seedance 1.0 系列（占位价，请用 --pricing 覆盖正式价）----
    "seedance-1-0-lite-t2v-250428":  {"input": 0, "output": 0.0007},
    "seedance-1-0-lite-i2v-250428":  {"input": 0, "output": 0.0007},
    "seedance-1-0-pro-250528":       {"input": 0, "output": 0.0014},
    "seedance-1-0-pro-fast-250528":  {"input": 0, "output": 0.001},

    # ---- Seedance 1.5 Pro（占位价）----
    "seedance-1-5-pro-251215":       {"input": 0, "output": 0.0017},

    # ---- Seedance 2.0 Pro（专业版）官方分档定价 ----
    # 来源：BytePlus 官方"sd2.0 专业版价格参考"
    "dreamina-seedance-2-0-260128": {
        "input": 0,
        "output": 0.00924,   # 默认走最贵的（无视频参考 1080p），保守 fallback
        "output_by_context": {
            "no_video_ref": {       # text-only / first_frame / reference_image / audio
                "480p":  0.0084,
                "720p":  0.0084,
                "1080p": 0.00924,
            },
            "with_video_ref": {     # 输入里包含 reference_video
                "480p":  0.00516,
                "720p":  0.00516,
                "1080p": 0.00564,
            },
        },
    },

    # ---- Seedance 2.0 Fast（占位价；如有官方分档表请同样写成 output_by_context）----
    "dreamina-seedance-2-0-fast-260128": {"input": 0, "output": 0.0014},

    # =========== Embedding ===========
    "embedding-vision-250615":     {"input": 0.00007, "output": 0.0},
    "embedding-vision-250328":     {"input": 0.00007, "output": 0.0},
    "doubao-embedding-text":       {"input": 0.00005, "output": 0.0},
}


def load_pricing_from_json(path: str) -> Dict[str, Dict[str, Any]]:
    """从外部 JSON 加载 / 合并价格。最常见用法是覆盖默认表。"""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        custom: Dict[str, Dict[str, Any]] = json.load(f)
    merged = {**PRICING, **custom}
    PRICING.clear()
    PRICING.update(merged)
    return PRICING


def get_price(model: str) -> Dict[str, Any]:
    """模糊匹配 model id（去掉日期后缀也能命中）。"""
    if model in PRICING:
        return PRICING[model]
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        if parts[0] in PRICING:
            return PRICING[parts[0]]
    return {}


def get_output_rate(model: str,
                    resolution: Optional[str] = None,
                    has_video_ref: bool = False) -> float:
    """挑视频模型的 output 单价（USD/1K tokens）。

    优先用 `output_by_context[resolution][bucket]`：
      - bucket = "with_video_ref" 当 has_video_ref=True
      - bucket = "no_video_ref"   当 has_video_ref=False
    缺失则回落到 flat `output`。

    用法：
        rate = get_output_rate("dreamina-seedance-2-0-260128",
                               resolution="1080p", has_video_ref=False)
        cost = tokens / 1000 * rate
    """
    p = get_price(model) or {}
    ctx = p.get("output_by_context")
    if ctx and resolution:
        bucket = "with_video_ref" if has_video_ref else "no_video_ref"
        rate = ctx.get(bucket, {}).get(resolution)
        if rate is not None:
            return rate
    return float(p.get("output", 0.0))
