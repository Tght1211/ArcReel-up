"""XiaoyunqueShortplayClient — mock VisualService，验证 req_key 分发 + 字段映射。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
from lib.xiaoyunque_shortplay.errors import XiaoyunqueAPIError


def _make_client(variant: str = "fast720p") -> XiaoyunqueShortplayClient:
    return XiaoyunqueShortplayClient(access_key="ak", secret_key="sk", model_variant=variant)


@pytest.mark.asyncio
async def test_submit_script_analysis_returns_task_id():
    client = _make_client()

    captured = {}

    def fake_submit(form: dict) -> dict:
        captured["form"] = form
        return {"code": 10000, "data": {"task_id": "tid-script-1"}}

    with patch.object(client._visual, "cv_sync2async_submit_task", side_effect=fake_submit):
        task_id = await client.submit_script_analysis(
            visual_style="真人写实",
            video_ratio="16:9",
            file_url="https://x/s.docx",
            file_type="docx",
            file_name="s.docx",
        )

    assert task_id == "tid-script-1"
    assert captured["form"]["req_key"] == "pippit_shortplay_cvtob_script_analysis"
    assert captured["form"]["visual_style"] == "真人写实"
    assert captured["form"]["file_url"] == "https://x/s.docx"


@pytest.mark.asyncio
async def test_submit_video_generate_picks_fast_or_pro_req_key():
    fast = _make_client(variant="fast720p")
    pro = _make_client(variant="pro720p")

    captured_fast, captured_pro = {}, {}

    def fake_submit_fast(form: dict) -> dict:
        captured_fast["form"] = form
        return {"code": 10000, "data": {"task_id": "f1"}}

    def fake_submit_pro(form: dict) -> dict:
        captured_pro["form"] = form
        return {"code": 10000, "data": {"task_id": "p1"}}

    with patch.object(fast._visual, "cv_sync2async_submit_task", side_effect=fake_submit_fast):
        await fast.submit_video_generate(assets_id="a1", thread_id="t1", episode_id="1", run_id="r-fast")

    with patch.object(pro._visual, "cv_sync2async_submit_task", side_effect=fake_submit_pro):
        await pro.submit_video_generate(assets_id="a1", thread_id="t1", episode_id="1", run_id="r-pro")

    assert captured_fast["form"]["req_key"] == "pippit_shortplay_cvtob_video_generate_fast720p"
    assert captured_pro["form"]["req_key"] == "pippit_shortplay_cvtob_video_generate_pro720p"


@pytest.mark.asyncio
async def test_non_10000_raises_xiaoyunque_api_error():
    client = _make_client()

    def fake_submit(form: dict) -> dict:
        return {"code": 50412, "data": None, "message": "Text Risk Not Pass"}

    with patch.object(client._visual, "cv_sync2async_submit_task", side_effect=fake_submit):
        with pytest.raises(XiaoyunqueAPIError) as exc_info:
            await client.submit_script_analysis(
                visual_style="x",
                video_ratio="16:9",
                file_url="https://x/s.docx",
                file_type="docx",
                file_name="s.docx",
            )
    assert exc_info.value.code == 50412


@pytest.mark.asyncio
async def test_query_script_analysis_parses_resp_data():
    client = _make_client()

    def fake_query(form: dict) -> dict:
        resp_data = (
            '{"thread_id":"ark_t1","assets_id":"ark_a1","status":"Success",'
            '"script_detail":{"CoreElement":{"EpisodeCount":3},'
            '"EpisodeAssets":[{"EpisodeID":"1","EpisodeTitle":"x","EpisodeAssetID":"ea1",'
            '"CharacterAssetIDs":["c1"],"SceneAssetIDs":["s1"]}]}}'
        )
        return {"code": 10000, "data": {"status": "done", "resp_data": resp_data}}

    with patch.object(client._visual, "cv_sync2async_get_result", side_effect=fake_query):
        result = await client.query_script_analysis("tid")

    assert result.status == "done"
    assert result.thread_id == "ark_t1"
    assert result.assets_id == "ark_a1"
    assert result.episode_count == 3
    assert len(result.episodes) == 1
    assert result.episodes[0].episode_id == "1"


@pytest.mark.asyncio
async def test_query_returns_in_progress_state_when_not_done():
    client = _make_client()

    def fake_query(form: dict) -> dict:
        return {"code": 10000, "data": {"status": "generating", "resp_data": ""}}

    with patch.object(client._visual, "cv_sync2async_get_result", side_effect=fake_query):
        result = await client.query_script_analysis("tid")

    assert result.status == "generating"
    assert result.thread_id is None
