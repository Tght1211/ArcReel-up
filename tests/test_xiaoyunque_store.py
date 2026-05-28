"""XiaoyunqueRunStore — CRUD + status transition + state_json 读写。"""

from __future__ import annotations

import pytest

from lib.xiaoyunque_shortplay.state import EpisodeState, RunState
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_create_then_get(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    assert run.id
    assert run.status == "pending"

    fetched = await store.get(run.id)
    assert fetched.id == run.id


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_set_status_advances_state(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_status(run.id, "parsing")
    refreshed = await store.get(run.id)
    assert refreshed.status == "parsing"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_state_roundtrip(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    s = RunState(parse_task_id="t1", episodes=[EpisodeState(episode_id="1")])
    await store.set_state(run.id, s)

    loaded = await store.get_state(run.id)
    assert loaded.parse_task_id == "t1"
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].episode_id == "1"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_set_failed_records_error(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_failed(run.id, "kaboom")
    fetched = await store.get(run.id)
    assert fetched.status == "failed"
    assert fetched.last_error == "kaboom"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_list_in_progress_excludes_terminal_states(async_session):
    store = XiaoyunqueRunStore(async_session)
    r_pending = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    r_done = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_status(r_done.id, "done")

    in_progress = await store.list_in_progress()
    ids = {r.id for r in in_progress}
    assert r_pending.id in ids
    assert r_done.id not in ids
