"""XiaoyunqueRun ORM model 基本读写测试。"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from lib.db.models.xiaoyunque_run import XiaoyunqueRun


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_can_insert_and_query(async_session):
    run = XiaoyunqueRun(
        id=str(uuid4()),
        project_name="p1",
        status="pending",
        model_variant="fast720p",
        visual_style="真人写实",
        video_ratio="16:9",
        script_file_url="https://x/script.docx",
        state_json=json.dumps({}),
    )
    async_session.add(run)
    await async_session.commit()

    fetched = (await async_session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.id == run.id))).scalar_one()
    assert fetched.project_name == "p1"
    assert fetched.status == "pending"
    assert fetched.model_variant == "fast720p"
    assert fetched.thread_id is None  # 可空字段缺省 None


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_status_transition_persists(async_session):
    run_id = str(uuid4())
    run = XiaoyunqueRun(
        id=run_id,
        project_name="p2",
        status="pending",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="9:16",
        script_file_url="https://x/s.docx",
        state_json="{}",
    )
    async_session.add(run)
    await async_session.commit()

    run.status = "parsing"
    run.thread_id = "ark_thread_x"
    await async_session.commit()

    fetched = (await async_session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.id == run_id))).scalar_one()
    assert fetched.status == "parsing"
    assert fetched.thread_id == "ark_thread_x"
