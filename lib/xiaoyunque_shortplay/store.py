"""XiaoyunqueRunStore — 异步 SQLAlchemy CRUD + state_json 序列化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.xiaoyunque_run import XiaoyunqueRun
from lib.xiaoyunque_shortplay.state import RunState, state_from_dict, state_to_dict

_TERMINAL_STATUSES = ("done", "failed", "cancelled")


class XiaoyunqueRunStore:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        project_name: str,
        model_variant: str,
        visual_style: str,
        video_ratio: str,
        script_file_url: str,
    ) -> XiaoyunqueRun:
        run = XiaoyunqueRun(
            id=uuid4().hex,
            project_name=project_name,
            status="pending",
            model_variant=model_variant,
            visual_style=visual_style,
            video_ratio=video_ratio,
            script_file_url=script_file_url,
            state_json="{}",
        )
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def get(self, run_id: str) -> XiaoyunqueRun:
        run = (
            await self._session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            raise LookupError(f"xiaoyunque run not found: {run_id}")
        return run

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        completed: bool = False,
    ) -> None:
        run = await self.get(run_id)
        run.status = status
        run.updated_at = datetime.now(UTC)
        if completed:
            run.completed_at = datetime.now(UTC)
        await self._session.commit()

    async def set_failed(self, run_id: str, error_message: str) -> None:
        run = await self.get(run_id)
        run.status = "failed"
        run.last_error = error_message
        run.updated_at = datetime.now(UTC)
        run.completed_at = datetime.now(UTC)
        await self._session.commit()

    async def set_state(self, run_id: str, state: RunState) -> None:
        run = await self.get(run_id)
        run.state_json = json.dumps(state_to_dict(state), ensure_ascii=False)
        run.updated_at = datetime.now(UTC)
        await self._session.commit()

    async def get_state(self, run_id: str) -> RunState:
        run = await self.get(run_id)
        return state_from_dict(json.loads(run.state_json or "{}"))

    async def set_thread_and_assets(
        self,
        run_id: str,
        *,
        thread_id: str,
        assets_id: str,
    ) -> None:
        run = await self.get(run_id)
        run.thread_id = thread_id
        run.assets_id = assets_id
        run.updated_at = datetime.now(UTC)
        await self._session.commit()

    async def list_in_progress(self) -> list[XiaoyunqueRun]:
        rows = (
            (await self._session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.status.not_in(_TERMINAL_STATUSES))))
            .scalars()
            .all()
        )
        return list(rows)

    async def list_by_project(
        self,
        project_name: str,
        *,
        status: str | None = None,
    ) -> list[XiaoyunqueRun]:
        stmt = select(XiaoyunqueRun).where(XiaoyunqueRun.project_name == project_name)
        if status:
            stmt = stmt.where(XiaoyunqueRun.status == status)
        stmt = stmt.order_by(XiaoyunqueRun.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)
