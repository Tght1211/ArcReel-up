"""mock 完整调用链：先 submit 拿 task_id，再轮询直到 done，下载 mp4。

不联网，纯本地 mock；目的是回归 backend 的 happy path 链路不会被
后续 refactor 打断。SDK 的 cv_sync2async_* 方法是 mock 目标。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 poll_with_retry 的 sleep 立刻返回，避免 15s 真等。"""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.mark.asyncio
async def test_end_to_end_flow_with_refs(tmp_path: Path) -> None:
    ref_img = tmp_path / "ref.png"
    ref_img.write_bytes(b"\x89PNG")

    req = VideoGenerationRequest(
        prompt="cat in space",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="16:9",
        duration_seconds=15,
        reference_images=[ref_img],
    )

    backend = VolcXiaoyunqueBackend(
        access_key="ak",
        secret_key="sk",
        tos_endpoint="tos-cn-beijing.volces.com",
        tos_bucket="b",
        tos_region="cn-beijing",
    )

    async def fake_upload(self: object, local_path: Path, **kw: object) -> str:
        return f"https://x/{local_path.name}"

    submit_payload = {"code": 10000, "data": {"task_id": "tid-99"}}
    poll_count = {"n": 0}

    def fake_submit(form: dict[str, object]) -> dict[str, object]:
        return submit_payload

    def fake_query(form: dict[str, object]) -> dict[str, object]:
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return {"code": 10000, "data": {"status": "generating"}}
        return {
            "code": 10000,
            "data": {
                "status": "done",
                "video_url": "https://x/out.mp4",
                "resp_data": '{"Duration": 15}',
            },
        }

    async def fake_download(url: str, output_path: Path, **kw: object) -> None:
        Path(output_path).write_bytes(b"\x00\x00\x00\x1cftypisom")

    with (
        patch("lib.volc_tos_uploader.TosImageUploader.upload_image", new=fake_upload),
        patch.object(backend._visual, "cv_sync2async_submit_task", side_effect=fake_submit),
        patch.object(backend._visual, "cv_sync2async_get_result", side_effect=fake_query),
        patch("lib.video_backends.volc_xiaoyunque.download_video", new=fake_download),
    ):
        result = await backend.generate(req)

    assert result.video_path == req.output_path
    assert result.video_path.exists()
    assert result.task_id == "tid-99"
    assert result.duration_seconds == 15
    assert poll_count["n"] >= 2


@pytest.mark.asyncio
async def test_e2e_raises_on_expired(tmp_path: Path) -> None:
    """status=expired 应中止任务（poll_with_retry 报 RuntimeError）。"""
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        duration_seconds=10,
    )
    backend = VolcXiaoyunqueBackend(
        access_key="ak",
        secret_key="sk",
        tos_endpoint="tos.x",
        tos_bucket="b",
        tos_region="cn-beijing",
    )

    def fake_submit(form: dict[str, object]) -> dict[str, object]:
        return {"code": 10000, "data": {"task_id": "t1"}}

    def fake_query(form: dict[str, object]) -> dict[str, object]:
        return {"code": 10000, "data": {"status": "expired"}}

    with (
        patch.object(backend._visual, "cv_sync2async_submit_task", side_effect=fake_submit),
        patch.object(backend._visual, "cv_sync2async_get_result", side_effect=fake_query),
    ):
        with pytest.raises(RuntimeError, match="过期|expired"):
            await backend.generate(req)


@pytest.mark.asyncio
async def test_e2e_raises_on_nonzero_business_code(tmp_path: Path) -> None:
    """code != 10000 且不在重试白名单（如 50412 Text Risk Not Pass）应立即抛出。"""
    req = VideoGenerationRequest(
        prompt="bad prompt",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        duration_seconds=10,
    )
    backend = VolcXiaoyunqueBackend(
        access_key="ak",
        secret_key="sk",
        tos_endpoint="tos.x",
        tos_bucket="b",
        tos_region="cn-beijing",
    )

    def fake_submit(form: dict[str, object]) -> dict[str, object]:
        return {"code": 10000, "data": {"task_id": "t1"}}

    def fake_query(form: dict[str, object]) -> dict[str, object]:
        return {"code": 50412, "data": None, "message": "Text Risk Not Pass"}

    with (
        patch.object(backend._visual, "cv_sync2async_submit_task", side_effect=fake_submit),
        patch.object(backend._visual, "cv_sync2async_get_result", side_effect=fake_query),
    ):
        with pytest.raises(RuntimeError, match="code=50412|Text Risk"):
            await backend.generate(req)


@pytest.mark.asyncio
async def test_e2e_retries_on_retryable_business_code(tmp_path: Path) -> None:
    """code=50500 Internal Error 应该 log warning + 继续轮询，下一轮 done 即成功。"""
    req = VideoGenerationRequest(
        prompt="ok prompt",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        duration_seconds=10,
    )
    backend = VolcXiaoyunqueBackend(
        access_key="ak",
        secret_key="sk",
        tos_endpoint="tos.x",
        tos_bucket="b",
        tos_region="cn-beijing",
    )

    call_count = {"n": 0}

    def fake_submit(form: dict[str, object]) -> dict[str, object]:
        return {"code": 10000, "data": {"task_id": "t1"}}

    def fake_query(form: dict[str, object]) -> dict[str, object]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"code": 50500, "data": None, "message": "Internal Error"}
        return {
            "code": 10000,
            "data": {"status": "done", "video_url": "https://x/out.mp4", "resp_data": "{}"},
        }

    async def fake_download(url: str, output_path: Path, **kw: object) -> None:
        Path(output_path).write_bytes(b"x")

    with (
        patch.object(backend._visual, "cv_sync2async_submit_task", side_effect=fake_submit),
        patch.object(backend._visual, "cv_sync2async_get_result", side_effect=fake_query),
        patch("lib.video_backends.volc_xiaoyunque.download_video", new=fake_download),
    ):
        result = await backend.generate(req)

    assert result.task_id == "t1"
    assert call_count["n"] >= 2  # 第一次 50500 重试，第二次 done
