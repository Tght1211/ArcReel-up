"""自定义供应商的 OpenAI 兼容图片生成探针 (POST /v1/images/generations)。

与 lib/config/anthropic_probe.py 同 pattern：httpx 直调，不走 SDK。
SDK 路径冷启动慢、stderr 不含 HTTP status、诊断精度差；httpx 能拿到精确
status_code 和上游错误 body，分类更可靠。

日志严格只打 model + status_code + latency_ms，不打 base64 / api_key / prompt。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageProbeResult:
    """单次生图测试的结果。失败时 image_b64 / revised_prompt / upstream_* 字段可能为 None。"""

    success: bool
    status_code: int | None
    latency_ms: int
    image_b64: str | None
    mime_type: str  # 默认 "image/png"; 若上游返回 output_format 则按其推导
    revised_prompt: str | None
    error: str | None  # 截断到 200 字符
    # 上游 metadata（仅展示, sub2api 中转会返, 其它 OpenAI 兼容上游不保证）
    upstream_model: str | None
    upstream_size: str | None
    upstream_quality: str | None
    upstream_output_format: str | None
