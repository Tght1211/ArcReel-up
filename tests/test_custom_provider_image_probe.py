"""lib/custom_provider/image_probe.py 单测：mock httpx 验证 probe 行为。

设计契约见 docs/superpowers/specs/2026-05-25-gpt-image-2-connection-test-design.md。
"""

from __future__ import annotations

from lib.custom_provider.image_probe import ImageProbeResult


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
