"""火山小云雀-智能生视频 Agent 2.0 后端。

调用走官方 volcengine.visual.VisualService，签名/重试/错误解析由 SDK 处理。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from volcengine.visual.VisualService import VisualService

from lib.providers import PROVIDER_VOLC_XIAOYUNQUE
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)
from lib.volc_tos_uploader import TosImageUploader

logger = logging.getLogger(__name__)

# 来自火山官方文档（85621/2359610「有参考」 + 85621/2359611「无参考」）
# - _with_vinput：支持图片 + 视频参考输入（img_url_list 必填）
# - 不带后缀：纯文生视频（img_url_list 可选）
# 控制台开通页：https://console.volcengine.com/ai/ability/detail/5
REQ_KEY_WITH_REFS = "pippit_iv2v_v20_cvtob_with_vinput"
REQ_KEY_WITHOUT_REFS = "pippit_iv2v_v20_cvtob"

# 业务错误码：可重试 vs 立即失败（不重试的多为内容审核/参数错误）
# 来自文档"业务错误码"表格；HTTP 200 + code=10000 才算成功
_RETRYABLE_BUSINESS_CODES = {
    50511,  # Post Img Risk Not Pass — 可重试
    50429,  # QPS 超限
    50430,  # 并发超限
    50500,  # Internal Error
    50501,  # Internal RPC Error
}


class VolcXiaoyunqueBackend:
    DEFAULT_MODEL = "xiaoyunque-agent-2.0"

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        tos_endpoint: str,
        tos_bucket: str,
        tos_region: str,
        language: str = "Chinese",
        model: str | None = None,
        **_ignored: object,
    ):
        self.ak = access_key
        self.sk = secret_key
        self.language = language
        self._model = model or self.DEFAULT_MODEL
        self._uploader = TosImageUploader(
            ak=access_key,
            sk=secret_key,
            endpoint=tos_endpoint,
            bucket=tos_bucket,
            region=tos_region,
        )
        # VisualService 是 singleton（内部有进程级 cache），通过 set_ak/set_sk 覆盖凭据
        self._visual = VisualService()
        self._visual.set_ak(access_key)
        self._visual.set_sk(secret_key)

    @property
    def name(self) -> str:
        return PROVIDER_VOLC_XIAOYUNQUE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(first_frame=True, reference_images=True, max_reference_images=50)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        ref_urls = await self._upload_refs(request)
        task_id, req_key = await self._submit(request, ref_urls)
        return await self._poll_until_done(task_id, req_key, request)

    async def _upload_refs(self, request: VideoGenerationRequest) -> list[str]:
        all_imgs: list[Path] = []
        if request.start_image:
            all_imgs.append(request.start_image)
        if request.end_image:
            all_imgs.append(request.end_image)
        if request.reference_images:
            all_imgs.extend(request.reference_images)

        urls: list[str] = []
        for p in all_imgs:
            if p.exists():
                urls.append(await self._uploader.upload_image(p))
        return urls

    async def _submit(self, request: VideoGenerationRequest, ref_urls: list[str]) -> tuple[str, str]:
        req_key = REQ_KEY_WITH_REFS if ref_urls else REQ_KEY_WITHOUT_REFS
        form: dict[str, Any] = {
            "req_key": req_key,
            "prompt": request.prompt,
            "ratio": request.aspect_ratio,
            "duration": self._duration_label(request.duration_seconds),
            "language": self.language,
            "enable_watermark": False,
        }
        if ref_urls:
            form["img_url_list"] = ref_urls

        # SDK 同步调用，丢到线程池
        data = await asyncio.to_thread(self._visual.cv_sync2async_submit_task, form)
        if not isinstance(data, dict):
            raise RuntimeError(f"submit returned non-dict: {data!r}")
        if data.get("code") != 10000:
            raise RuntimeError(f"submit failed: {data}")
        task_data = data.get("data")
        if not isinstance(task_data, dict):
            raise RuntimeError(f"submit returned unexpected data type: {task_data!r}")
        task_id = str(task_data["task_id"])
        logger.info("小云雀任务已提交 task_id=%s req_key=%s", task_id, req_key)
        return task_id, req_key

    async def _poll_until_done(
        self,
        task_id: str,
        req_key: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        async def _query() -> dict[str, object]:
            form = {"req_key": req_key, "task_id": task_id}
            return await asyncio.to_thread(self._visual.cv_sync2async_get_result, form)

        def _get_status(r: dict[str, object]) -> str:
            d = r.get("data")
            if isinstance(d, dict):
                return str(d.get("status", ""))
            return ""

        def _check_failed(r: dict[str, object]) -> str | None:
            # 文档明确要求：优先判断 code=10000，再判断 data.status
            code = r.get("code")
            if code != 10000:
                if isinstance(code, int) and code in _RETRYABLE_BUSINESS_CODES:
                    # 瞬态错误：返回 None 让 poll_with_retry 继续下一轮，并 log warning
                    logger.warning("小云雀查询瞬态错误 code=%s message=%s 将重试", code, r.get("message"))
                    return None
                return f"小云雀查询失败 code={code} message={r.get('message')!r}"
            status = _get_status(r)
            if status in ("not_found", "expired"):
                return f"小云雀任务过期/丢失: status={status} response={r}"
            return None

        result = await poll_with_retry(
            poll_fn=_query,
            is_done=lambda r: r.get("code") == 10000 and _get_status(r) == "done",
            is_failed=_check_failed,
            poll_interval=15,
            max_wait=1200,
            label="Xiaoyunque",
            on_progress=lambda r, elapsed: logger.info(
                "小云雀状态: %s, 已等 %ds",
                _get_status(r),
                int(elapsed),
            ),
        )

        raw_data = result.get("data")
        if not isinstance(raw_data, dict):
            raise RuntimeError(f"小云雀返回 data 非 dict: {result}")
        data = raw_data

        if data.get("status") != "done":
            raise RuntimeError(f"小云雀异常状态: {data}")

        video_url = data.get("video_url")
        if not isinstance(video_url, str) or not video_url:
            raise RuntimeError(f"小云雀返回缺 video_url: {data}")
        await download_video(video_url, request.output_path)

        # 解析 resp_data 拿真实时长
        resp_data = data.get("resp_data")
        actual_duration = request.duration_seconds
        if isinstance(resp_data, str):
            try:
                rd = json.loads(resp_data)
                actual_duration = int(rd.get("Duration", actual_duration))
            except (ValueError, TypeError):
                pass

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_VOLC_XIAOYUNQUE,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            task_id=task_id,
        )

    @staticmethod
    def _duration_label(seconds: int) -> str:
        if seconds <= 15:
            return "~15s"
        if seconds <= 30:
            return "~30s"
        if seconds <= 60:
            return "40~60s"
        raise ValueError(f"duration {seconds}s 超出小云雀支持范围（≤ 60s）")
