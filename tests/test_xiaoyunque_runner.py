"""XiaoyunquePipelineRunner — 串通 happy path + 单集失败 + cancelled。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lib.xiaoyunque_shortplay.client import (
    EpisodeAssetInfo,
    MaterialDesignResult,
    ScriptAnalysisResult,
    ShotInfo,
    VideoComposeResult,
    VideoGenerateResult,
)
from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_happy_path_completes_all_steps(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )

    fake = AsyncMock()
    fake.submit_script_analysis.return_value = "task-script"
    fake.query_script_analysis.return_value = ScriptAnalysisResult(
        status="done",
        thread_id="ark_t1",
        assets_id="ark_a1",
        episode_count=1,
        episodes=[EpisodeAssetInfo(episode_id="1", episode_title="x", episode_asset_id="ea1")],
    )
    fake.submit_material_design.return_value = "task-design"
    fake.query_material_design.return_value = MaterialDesignResult(
        status="done",
        characters=[],
        scenes=[],
        image_count=2,
    )
    fake.submit_video_generate.return_value = "task-vg-1"
    fake.query_video_generate.return_value = VideoGenerateResult(
        status="done",
        storyboard_status_map={"S1": 3},
        shots=[ShotInfo(shot_id="S1", description="x", status=3, video_url="https://x/s1.mp4")],
    )
    fake.submit_video_compose.return_value = "task-vc-1"
    fake.query_video_compose.return_value = VideoComposeResult(
        status="done",
        final_video_url="https://x/ep1.mp4",
        final_cover_url="https://x/ep1.png",
    )

    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)

    final = await store.get(run.id)
    assert final.status == "done"
    assert final.thread_id == "ark_t1"

    state = await store.get_state(run.id)
    assert state.episodes[0].final_video_url == "https://x/ep1.mp4"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_single_episode_video_failure_marks_episode_then_continues(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )

    fake = AsyncMock()
    fake.submit_script_analysis.return_value = "tsa"
    fake.query_script_analysis.return_value = ScriptAnalysisResult(
        status="done",
        thread_id="t",
        assets_id="a",
        episode_count=2,
        episodes=[
            EpisodeAssetInfo(episode_id="1", episode_asset_id="ea1"),
            EpisodeAssetInfo(episode_id="2", episode_asset_id="ea2"),
        ],
    )
    fake.submit_material_design.return_value = "tmd"
    fake.query_material_design.return_value = MaterialDesignResult(status="done")
    fake.submit_video_generate.return_value = "tvg"

    # 集 1 失败 (status=failed)，集 2 成功
    vg_responses = [
        VideoGenerateResult(status="failed"),  # 集 1
        VideoGenerateResult(status="done", shots=[]),  # 集 2
    ]
    fake.query_video_generate.side_effect = vg_responses
    fake.submit_video_compose.return_value = "tvc"
    fake.query_video_compose.return_value = VideoComposeResult(
        status="done",
        final_video_url="https://x/ep2.mp4",
    )

    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)

    final = await store.get(run.id)
    state = await store.get_state(run.id)
    assert final.status == "done"
    ep1, ep2 = state.episodes
    assert ep1.status == "failed"
    assert ep2.status == "done"
    assert ep2.final_video_url == "https://x/ep2.mp4"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_cancel_during_run_aborts_early(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_status(run.id, "cancelled")

    fake = AsyncMock()
    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)  # 应直接 noop 返回

    fake.submit_script_analysis.assert_not_called()
