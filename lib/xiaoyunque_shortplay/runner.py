"""XiaoyunquePipelineRunner — 4 步 pipeline 编排。

可恢复设计：每步成功立即写库（status 进入下一阶段），重启后从最新 status 继续。
"""

from __future__ import annotations

import asyncio
import logging

from lib.xiaoyunque_shortplay.client import (
    MaterialDesignResult,
    ScriptAnalysisResult,
    XiaoyunqueShortplayClient,
)
from lib.xiaoyunque_shortplay.errors import (
    PipelineCancelled,
    XiaoyunqueAPIError,
    XiaoyunqueError,
    classify_business_code,
)
from lib.xiaoyunque_shortplay.state import (
    CharacterState,
    EpisodeState,
    RunState,
    SceneState,
    ScriptSummary,
    ShotState,
)
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15
MAX_WAIT_SEC_PER_STEP = 1800  # 30 min/step


class XiaoyunquePipelineRunner:
    """编排 4 步 pipeline。每步 store.set_state + set_status 推进，断点续传安全。"""

    def __init__(self, client: XiaoyunqueShortplayClient, store: XiaoyunqueRunStore):
        self.client = client
        self.store = store

    async def run(self, run_id: str) -> None:
        run = await self.store.get(run_id)

        if run.status in ("done", "failed", "cancelled"):
            logger.info("run %s 已经在终态 %s，跳过", run_id, run.status)
            return

        try:
            await self._execute(run_id)
            await self.store.set_status(run_id, "done", completed=True)
        except PipelineCancelled:
            logger.info("run %s 被取消", run_id)
        except XiaoyunqueError as e:
            logger.exception("run %s 失败", run_id)
            await self.store.set_failed(run_id, str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("run %s 异常", run_id)
            await self.store.set_failed(run_id, f"unexpected: {e}")

    async def _execute(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        if run.status == "pending":
            await self._step1_parse(run_id)

        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "parsing":
            await self._step2_design(run_id)

        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "designing":
            await self._step3_video_generate(run_id)

        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "generating":
            await self._step4_video_compose(run_id)

    @staticmethod
    async def _check_cancelled(status: str, run_id: str) -> None:
        if status == "cancelled":
            raise PipelineCancelled(run_id)

    async def _step1_parse(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        state = await self.store.get_state(run_id)

        if not state.parse_task_id:
            task_id = await self.client.submit_script_analysis(
                visual_style=run.visual_style,
                video_ratio=run.video_ratio,
                file_url=run.script_file_url,
                file_type=_extract_file_type(run.script_file_url),
                file_name=_extract_file_name(run.script_file_url),
            )
            state.parse_task_id = task_id
            await self.store.set_state(run_id, state)
            logger.info("xiaoyunque run %s 剧本解析提交 task=%s", run_id, task_id)

        result = await self._wait_for(
            lambda: self.client.query_script_analysis(state.parse_task_id or ""),
            label=f"run={run_id} step=script_analysis",
        )
        await self._record_script(run_id, state, result)
        await self.store.set_status(run_id, "parsing")

    async def _record_script(
        self,
        run_id: str,
        state: RunState,
        result: ScriptAnalysisResult,
    ) -> None:
        if not result.thread_id or not result.assets_id:
            raise XiaoyunqueError(f"剧本解析未返回 thread_id/assets_id: {result}")
        await self.store.set_thread_and_assets(
            run_id,
            thread_id=result.thread_id,
            assets_id=result.assets_id,
        )
        state.script = ScriptSummary(episode_count=result.episode_count)
        state.episodes = [
            EpisodeState(
                episode_id=e.episode_id,
                episode_asset_id=e.episode_asset_id,
                title=e.episode_title,
            )
            for e in result.episodes
        ]
        state.charge_count_total += result.charge_count
        await self.store.set_state(run_id, state)

    async def _step2_design(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        state = await self.store.get_state(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step2 缺少 thread_id/assets_id: run_id={run_id}")

        if not state.design_task_id:
            task_id = await self.client.submit_material_design(
                assets_id=run.assets_id,
                thread_id=run.thread_id,
                run_id=run_id,
            )
            state.design_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_material_design(state.design_task_id or ""),
            label=f"run={run_id} step=material_design",
        )
        await self._record_design(run_id, state, result)
        await self.store.set_status(run_id, "designing")

    async def _record_design(
        self,
        run_id: str,
        state: RunState,
        result: MaterialDesignResult,
    ) -> None:
        state.characters = [
            CharacterState(
                character_id=c.character_id,
                name=c.character_name,
                body_image_url=c.body_image_url,
                bust_portrait_url=c.bust_portrait_url,
                appearance_count=c.actual_render_count,
            )
            for c in result.characters
        ]
        state.scenes = [SceneState(scene_id=s.scene_id, name=s.name, image_urls=s.image_urls) for s in result.scenes]
        state.charge_count_total += result.image_count
        await self.store.set_state(run_id, state)

    async def _step3_video_generate(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step3 缺少 thread_id/assets_id: run_id={run_id}")

        await self.store.set_status(run_id, "generating")

        state = await self.store.get_state(run_id)
        for episode in state.episodes:
            await self._check_cancelled((await self.store.get(run_id)).status, run_id)
            if episode.status in ("done", "composing"):
                continue

            try:
                await self._generate_one_episode(run_id, run.thread_id, run.assets_id, state, episode)
            except XiaoyunqueAPIError as e:
                if classify_business_code(e.code) == "fatal":
                    episode.status = "failed"
                    episode.error_message = str(e)
                    await self.store.set_state(run_id, state)
                    continue
                raise

    async def _generate_one_episode(
        self,
        run_id: str,
        thread_id: str,
        assets_id: str,
        state: RunState,
        episode: EpisodeState,
    ) -> None:
        """episode 是 state.episodes 中的同一对象引用，调用方负责 set_state。"""
        if not episode.video_generate_task_id:
            task_id = await self.client.submit_video_generate(
                assets_id=assets_id,
                thread_id=thread_id,
                episode_id=episode.episode_id,
                run_id=run_id,
            )
            episode.video_generate_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_video_generate(episode.video_generate_task_id or ""),
            label=f"run={run_id} ep={episode.episode_id} step=video_generate",
        )

        if result.status != "done":
            episode.status = "failed"
            episode.error_message = f"视频生成失败 status={result.status}"
            state.charge_count_total += result.charge_count
            await self.store.set_state(run_id, state)
            return

        episode.shots = [
            ShotState(
                shot_id=s.shot_id,
                description=s.description,
                status=s.status,
                video_url=s.video_url,
                duration_ms=s.duration_ms,
            )
            for s in result.shots
        ]
        if any(s.status != 3 for s in result.shots):
            episode.status = "failed"
            episode.error_message = f"部分分镜失败: {result.storyboard_status_map}"
        else:
            episode.status = "composing"
        state.charge_count_total += result.charge_count
        await self.store.set_state(run_id, state)

    async def _step4_video_compose(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step4 缺少 thread_id/assets_id: run_id={run_id}")

        await self.store.set_status(run_id, "composing")

        state = await self.store.get_state(run_id)
        for episode in state.episodes:
            await self._check_cancelled((await self.store.get(run_id)).status, run_id)
            if episode.status != "composing":
                continue

            try:
                await self._compose_one_episode(run_id, run.thread_id, run.assets_id, state, episode)
            except XiaoyunqueAPIError as e:
                episode.status = "failed_compose"
                episode.error_message = str(e)
                await self.store.set_state(run_id, state)
                continue

    async def _compose_one_episode(
        self,
        run_id: str,
        thread_id: str,
        assets_id: str,
        state: RunState,
        episode: EpisodeState,
    ) -> None:
        """episode 是 state.episodes 中的同一对象引用，调用方负责处理异常后的 set_state。"""
        if not episode.video_compose_task_id:
            task_id = await self.client.submit_video_compose(
                assets_id=assets_id,
                thread_id=thread_id,
                episode_id=episode.episode_id,
            )
            episode.video_compose_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_video_compose(episode.video_compose_task_id or ""),
            label=f"run={run_id} ep={episode.episode_id} step=video_compose",
        )
        episode.final_video_url = result.final_video_url
        episode.final_cover_url = result.final_cover_url
        episode.status = "done"
        await self.store.set_state(run_id, state)

    async def _wait_for(self, query_fn, *, label: str):  # noqa: ANN001 ANN201
        start = asyncio.get_event_loop().time()
        while True:
            try:
                result = await query_fn()
            except XiaoyunqueAPIError as e:
                if classify_business_code(e.code) == "retryable":
                    logger.warning("%s 瞬态错误 code=%s 将重试", label, e.code)
                else:
                    raise
            else:
                status = result.status
                if status in ("done", "failed"):
                    return result
                if status in ("not_found", "expired"):
                    raise XiaoyunqueError(f"{label} 任务过期/丢失 status={status}")
                logger.info("%s 状态=%s", label, status)

            if asyncio.get_event_loop().time() - start >= MAX_WAIT_SEC_PER_STEP:
                raise XiaoyunqueError(f"{label} 超时 ({MAX_WAIT_SEC_PER_STEP}s)")
            await asyncio.sleep(POLL_INTERVAL_SEC)


def _extract_file_name(url: str) -> str:
    """从 TOS 签名 URL 取文件名（不含 query）。"""
    path = url.split("?", 1)[0]
    return path.rsplit("/", 1)[-1] or "script.docx"


def _extract_file_type(url: str) -> str:
    name = _extract_file_name(url)
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return "docx"
