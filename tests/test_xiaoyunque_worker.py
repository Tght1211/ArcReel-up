"""XiaoyunqueWorker — 启动时拉未完成 run，每个起一个 asyncio task。"""

from __future__ import annotations

import pytest

from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from lib.xiaoyunque_shortplay.worker import XiaoyunqueWorker


class _FakeRunner:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self._cb = callback

    async def run(self, run_id: str) -> None:
        await self._cb(run_id)


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_worker_picks_up_in_progress_runs_on_start(async_session):
    store = XiaoyunqueRunStore(async_session)
    r1 = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    r2 = await store.create(
        project_name="p2",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_status(r2.id, "done")  # 终态，应被跳过

    runs_seen: list[str] = []

    async def fake_run(run_id: str) -> None:
        runs_seen.append(run_id)

    worker = XiaoyunqueWorker(
        runner_factory=lambda store_arg: _FakeRunner(fake_run),
        store_factory=lambda: store,
    )
    await worker.start()
    await worker.wait_idle(timeout=2.0)
    await worker.stop()

    assert r1.id in runs_seen
    assert r2.id not in runs_seen


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_worker_can_be_triggered_for_new_run(async_session):
    store = XiaoyunqueRunStore(async_session)
    r = await store.create(
        project_name="p1",
        model_variant="fast720p",
        visual_style="x",
        video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )

    runs_seen: list[str] = []

    async def fake_run(run_id: str) -> None:
        runs_seen.append(run_id)

    worker = XiaoyunqueWorker(
        runner_factory=lambda store_arg: _FakeRunner(fake_run),
        store_factory=lambda: store,
    )
    await worker.start()
    worker.trigger(r.id)
    await worker.wait_idle(timeout=2.0)
    await worker.stop()

    assert r.id in runs_seen
