"""自定义供应商的 OpenAI 兼容图片生成探针 (POST /v1/images/generations)。

与 lib/config/anthropic_probe.py 同 pattern：httpx 直调，不走 SDK。
SDK 路径冷启动慢、stderr 不含 HTTP status、诊断精度差；httpx 能拿到精确
status_code 和上游错误 body，分类更可靠。

日志严格只打 model + status_code + latency_ms，不打 base64 / api_key / prompt。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200
_DEFAULT_TIMEOUT_S = 120.0  # 实测 sub2api 中转 gpt-image-2 ~62s，留 2x buffer


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


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> httpx.Response:
    """间接层：测试时 patch 这一个（与 anthropic_probe._post 同 pattern）。"""
    client = get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


def _mime_from_output_format(output_format: str | None) -> str:
    """把上游 output_format 字符串映射到 MIME，未知或缺失时回退 image/png。"""
    if not output_format:
        return "image/png"
    fmt = output_format.lower().strip()
    if fmt in ("png", "image/png"):
        return "image/png"
    if fmt in ("jpeg", "jpg", "image/jpeg"):
        return "image/jpeg"
    if fmt in ("webp", "image/webp"):
        return "image/webp"
    return "image/png"


async def probe_image_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> ImageProbeResult:
    """POST {base_url}/v1/images/generations 发最小请求 (n=1, response_format=b64_json)。

    判定:
    - 2xx + data[0].b64_json 非空 → success
    - 2xx 但 data=[] / 缺字段 / JSON 解析失败 → 失败 (error 含原因)
    - 非 2xx → 失败 (上游 body 截 200 字符)
    - timeout / 网络异常 → 失败 (status_code=None)

    日志只打 model + status_code + latency_ms，绝不打 base64 / api_key / prompt。
    """
    url = f"{base_url.rstrip('/')}/v1/images/generations"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    elapsed = int((time.perf_counter() - started) * 1000)

    logger.info(
        "probe_image_generation model=%s status=%d elapsed_ms=%d",
        model,
        resp.status_code,
        elapsed,
    )

    if resp.status_code >= 400:
        return ImageProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error=_truncate(resp.text),
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )

    try:
        body = resp.json()
    except ValueError:
        return ImageProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error="upstream returned non-JSON body",
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        return ImageProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error="upstream returned 200 with empty data (likely content filter or upstream issue)",
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )

    first = data[0] if isinstance(data[0], dict) else {}
    b64 = first.get("b64_json") if isinstance(first.get("b64_json"), str) else None
    if not b64:
        return ImageProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error="upstream data[0] missing b64_json field",
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )

    output_format = body.get("output_format") if isinstance(body.get("output_format"), str) else None
    return ImageProbeResult(
        success=True,
        status_code=resp.status_code,
        latency_ms=elapsed,
        image_b64=b64,
        mime_type=_mime_from_output_format(output_format),
        revised_prompt=first.get("revised_prompt") if isinstance(first.get("revised_prompt"), str) else None,
        error=None,
        upstream_model=body.get("model") if isinstance(body.get("model"), str) else None,
        upstream_size=body.get("size") if isinstance(body.get("size"), str) else None,
        upstream_quality=body.get("quality") if isinstance(body.get("quality"), str) else None,
        upstream_output_format=output_format,
    )
