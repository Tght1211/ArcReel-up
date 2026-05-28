"""XiaoyunqueWorker — 后台运行 pipeline，单进程，每个 run 一个 asyncio task。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore

logger = logging.getLogger(__name__)


class XiaoyunqueWorker:
    """后台 runner 池。lifespan startup 时 start()，shutdown 时 stop()。"""

    def __init__(
        self,
        *,
        runner_factory: Callable[[XiaoyunqueRunStore], XiaoyunquePipelineRunner],
        store_factory: Callable[[], XiaoyunqueRunStore],
    ):
        self._runner_factory = runner_factory
        self._store_factory = store_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        store = self._store_factory()
        in_progress = await store.list_in_progress()
        for run in in_progress:
            self._spawn(run.id)
        logger.info("XiaoyunqueWorker 启动，拉起 %d 个在途 run", len(in_progress))

    def trigger(self, run_id: str) -> None:
        if not self._started:
            logger.warning("worker 未启动，trigger %s 被忽略", run_id)
            return
        if run_id in self._tasks and not self._tasks[run_id].done():
            logger.info("run %s 已在跑，跳过", run_id)
            return
        self._spawn(run_id)

    def _spawn(self, run_id: str) -> None:
        async def _runner_task() -> None:
            store = self._store_factory()
            runner = self._runner_factory(store)
            try:
                await runner.run(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("runner task for run %s 异常", run_id)
            finally:
                self._tasks.pop(run_id, None)

        self._tasks[run_id] = asyncio.create_task(_runner_task(), name=f"xiaoyunque-{run_id}")

    async def wait_idle(self, *, timeout: float = 30.0) -> None:
        """等待当前所有 task 完成（测试用）。"""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(
            list(self._tasks.values()), timeout=timeout, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            for t in pending:
                t.cancel()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("XiaoyunqueWorker 已停止")
