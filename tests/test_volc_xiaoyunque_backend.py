"""VolcXiaoyunqueBackend — mock httpx + mock TOS uploader。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend


def _make_backend() -> VolcXiaoyunqueBackend:
    return VolcXiaoyunqueBackend(
        access_key="ak",
        secret_key="sk",
        tos_endpoint="tos-cn-beijing.volces.com",
        tos_bucket="b",
        tos_region="cn-beijing",
    )


@pytest.mark.asyncio
async def test_with_refs_picks_with_vinput_reqkey(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    req = VideoGenerationRequest(
        prompt="cat walks",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        duration_seconds=10,
        reference_images=[img],
    )
    submitted_payload = {}

    async def fake_submit(self, request, ref_urls):
        submitted_payload["ref_urls"] = ref_urls
        return "tid-1"

    async def fake_poll(self, task_id, request):
        from lib.video_backends.base import VideoGenerationResult

        return VideoGenerationResult(
            video_path=request.output_path,
            provider="volc-xiaoyunque",
            model="xiaoyunque-agent-2.0",
            duration_seconds=request.duration_seconds,
        )

    async def fake_upload(self, local_path, **kw):
        return f"https://x/{local_path.name}"

    with (
        patch.object(VolcXiaoyunqueBackend, "_submit", new=fake_submit),
        patch.object(VolcXiaoyunqueBackend, "_poll_until_done", new=fake_poll),
        patch("lib.volc_tos_uploader.TosImageUploader.upload_image", new=fake_upload),
    ):
        backend = _make_backend()
        result = await backend.generate(req)

    assert result.video_path == req.output_path
    assert submitted_payload["ref_urls"]


def test_duration_mapping():
    backend = _make_backend()
    assert backend._duration_label(10) == "~15s"
    assert backend._duration_label(20) == "~30s"
    assert backend._duration_label(60) == "40~60s"
    with pytest.raises(ValueError):
        backend._duration_label(120)


def test_capabilities():
    backend = _make_backend()
    from lib.video_backends.base import VideoCapability

    assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
    assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
    assert backend.video_capabilities.reference_images is True
    assert backend.video_capabilities.max_reference_images == 50
