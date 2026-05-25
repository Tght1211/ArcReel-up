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


class TestProbeImageGenerationErrors:
    @pytest.mark.asyncio
    async def test_401_returns_truncated_error(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(
            return_value=_make_resp(
                status_code=401,
                text='{"error":{"message":"invalid_api_key"}}',
            )
        )
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="bad", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code == 401
        assert "invalid_api_key" in (r.error or "")
        assert r.image_b64 is None

    @pytest.mark.asyncio
    async def test_429_rate_limited(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=429, text="rate limited"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code == 429

    @pytest.mark.asyncio
    async def test_5xx_returns_status(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=502, text="bad gateway"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code == 502

    @pytest.mark.asyncio
    async def test_200_with_empty_data_fails(self):
        """sub2api 实测：内容安全过滤时上游可能返 200 + data:[]."""
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body={"data": []}))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code == 200
        assert "empty data" in (r.error or "")

    @pytest.mark.asyncio
    async def test_200_with_missing_data_field_fails(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body={"foo": "bar"}))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False

    @pytest.mark.asyncio
    async def test_200_with_non_json_body_fails(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, text="<html>oops</html>"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert "non-JSON" in (r.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_status_none(self):
        from lib.custom_provider import image_probe

        async def _raise_timeout(**_kw):
            raise httpx.TimeoutException("read timeout")

        with patch.object(image_probe, "_post", AsyncMock(side_effect=_raise_timeout)):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code is None
        assert "timeout" in (r.error or "").lower()

    @pytest.mark.asyncio
    async def test_network_error_returns_status_none(self):
        from lib.custom_provider import image_probe

        async def _raise_conn(**_kw):
            raise httpx.ConnectError("conn refused")

        with patch.object(image_probe, "_post", AsyncMock(side_effect=_raise_conn)):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.success is False
        assert r.status_code is None

    @pytest.mark.asyncio
    async def test_error_body_truncated_to_200_chars(self):
        from lib.custom_provider import image_probe

        long_body = "x" * 5000
        mock_post = AsyncMock(return_value=_make_resp(status_code=500, text=long_body))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p"
            )
        assert r.error is not None
        # 200 个 'x' + 1 个 '…' 字符（ellipsis 是单字符，不是三点）
        assert len(r.error) <= 201
        assert r.error.endswith("…")

    @pytest.mark.asyncio
    async def test_log_does_not_contain_api_key_or_base64(self, caplog):
        """日志安全性: 不允许打 api_key 或 base64 内容."""
        import logging as _logging

        from lib.custom_provider import image_probe

        mock_post = AsyncMock(
            return_value=_make_resp(
                status_code=200,
                json_body={"data": [{"b64_json": "SECRETBASE64DATAXYZ"}]},
            )
        )
        with caplog.at_level(_logging.INFO, logger="lib.custom_provider.image_probe"):
            with patch.object(image_probe, "_post", mock_post):
                await image_probe.probe_image_generation(
                    base_url="https://x",
                    api_key="sk-supersecret-key",
                    model="gpt-image-2",
                    prompt="hello",
                )
        full_log = " ".join(rec.getMessage() for rec in caplog.records)
        assert "sk-supersecret-key" not in full_log
        assert "SECRETBASE64DATAXYZ" not in full_log
