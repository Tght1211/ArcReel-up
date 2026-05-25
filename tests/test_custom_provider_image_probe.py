"""lib/custom_provider/image_probe.py 单测：mock httpx 验证 probe 行为。

设计契约见 docs/superpowers/specs/2026-05-25-gpt-image-2-connection-test-design.md。
"""

from __future__ import annotations

import json as _json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lib.custom_provider.image_probe import ImageProbeResult


def _make_resp(*, status_code: int, json_body: dict | None = None, text: str | None = None) -> httpx.Response:
    """构造 httpx.Response 用作 mock 返回值；request 必填否则部分属性会 raise。"""
    req = httpx.Request("POST", "https://upstream.test/v1/images/generations")
    if json_body is not None:
        return httpx.Response(status_code, request=req, content=_json.dumps(json_body).encode())
    return httpx.Response(status_code, request=req, text=text or "")


class TestImageProbeResultShape:
    def test_dataclass_fields(self):
        r = ImageProbeResult(
            success=True,
            status_code=200,
            latency_ms=12345,
            image_b64="iVBOR...",
            mime_type="image/png",
            revised_prompt="A cat",
            error=None,
            upstream_model="gpt-image-2",
            upstream_size="auto",
            upstream_quality="auto",
            upstream_output_format="png",
        )
        assert r.success is True
        assert r.latency_ms == 12345
        assert r.image_b64 == "iVBOR..."
        assert r.upstream_output_format == "png"

    def test_dataclass_frozen(self):
        import dataclasses

        r = ImageProbeResult(
            success=False,
            status_code=None,
            latency_ms=0,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error="boom",
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )
        try:
            r.success = True  # type: ignore[misc]
            raise AssertionError("expected frozen dataclass to reject mutation")
        except dataclasses.FrozenInstanceError:
            pass


class TestProbeImageGenerationHappyPath:
    @pytest.mark.asyncio
    async def test_success_returns_image_and_metadata(self):
        from lib.custom_provider import image_probe

        upstream_body = {
            "data": [{"b64_json": "iVBORw0KGgoAAAA", "revised_prompt": "A cute cat sticker"}],
            "model": "gpt-image-2",
            "output_format": "png",
            "size": "1024x1024",
            "quality": "high",
            "usage": {"input_tokens": 64, "output_tokens": 1372},
        }
        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body=upstream_body))

        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://upstream.test",
                api_key="sk-test",
                model="gpt-image-2",
                prompt="A cat",
            )

        assert r.success is True
        assert r.status_code == 200
        assert r.image_b64 == "iVBORw0KGgoAAAA"
        assert r.revised_prompt == "A cute cat sticker"
        assert r.mime_type == "image/png"
        assert r.upstream_model == "gpt-image-2"
        assert r.upstream_size == "1024x1024"
        assert r.upstream_quality == "high"
        assert r.upstream_output_format == "png"
        assert r.error is None
        assert r.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_post_called_with_correct_url_payload_and_headers(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(
            return_value=_make_resp(
                status_code=200,
                json_body={"data": [{"b64_json": "x"}]},
            )
        )

        with patch.object(image_probe, "_post", mock_post):
            await image_probe.probe_image_generation(
                base_url="https://upstream.test/",  # 带尾斜杠
                api_key="sk-abc",
                model="gpt-image-2",
                prompt="hello",
            )

        kwargs = mock_post.call_args.kwargs
        assert kwargs["url"] == "https://upstream.test/v1/images/generations"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-abc"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["payload"] == {
            "model": "gpt-image-2",
            "prompt": "hello",
            "n": 1,
            "response_format": "b64_json",
        }
        assert kwargs["timeout_s"] == 120.0
