"""火山小云雀-智能生视频 Agent 2.0 后端。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

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
from lib.volc_visual_shared import VISUAL_HOST, sign_request

logger = logging.getLogger(__name__)

# 文档已知
REQ_KEY_WITH_REFS = "pippit_iv2v_v20_cvtob_with_vinput"
# TODO（实施前需补全）：见 spec §13。占位字串确保走 with_refs 路径前不会误用。
REQ_KEY_WITHOUT_REFS = "TODO_NO_REF_REQ_KEY"

# TODO（实施前需 AK/SK 探活确认）：按 Volcengine "Visual" 服务一贯命名先用 GetResult
SUBMIT_ACTION = "CVSync2AsyncSubmitTask"
QUERY_ACTION = "CVSync2AsyncGetResult"
API_VERSION = "2022-08-31"


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
        task_id = await self._submit(request, ref_urls)
        return await self._poll_until_done(task_id, request)

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

    async def _submit(self, request: VideoGenerationRequest, ref_urls: list[str]) -> str:
        req_key = REQ_KEY_WITH_REFS if ref_urls else REQ_KEY_WITHOUT_REFS
        body: dict[str, object] = {
            "req_key": req_key,
            "prompt": request.prompt,
            "ratio": request.aspect_ratio,
            "duration": self._duration_label(request.duration_seconds),
            "language": self.language,
            "enable_watermark": False,
        }
        if ref_urls:
            body["img_url_list"] = ref_urls

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        query = {"Action": SUBMIT_ACTION, "Version": API_VERSION}
        headers = sign_request(
            method="POST",
            host=VISUAL_HOST,
            path="/",
            query=query,
            headers={"Content-Type": "application/json"},
            body=payload,
            access_key=self.ak,
            secret_key=self.sk,
        )
        url = f"https://{VISUAL_HOST}/?Action={SUBMIT_ACTION}&Version={API_VERSION}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, content=payload, headers=headers)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()

        if data.get("code") != 10000:
            raise RuntimeError(f"submit failed: {data}")
        task_data = data["data"]
        if not isinstance(task_data, dict):
            raise RuntimeError(f"submit returned unexpected data type: {task_data!r}")
        task_id = str(task_data["task_id"])
        logger.info("小云雀任务已提交 task_id=%s req_key=%s", task_id, req_key)
        return task_id

    async def _poll_until_done(
        self,
        task_id: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        async def _query() -> dict[str, object]:
            body = json.dumps({"req_key": REQ_KEY_WITH_REFS, "task_id": task_id}).encode("utf-8")
            query = {"Action": QUERY_ACTION, "Version": API_VERSION}
            headers = sign_request(
                method="POST",
                host=VISUAL_HOST,
                path="/",
                query=query,
                headers={"Content-Type": "application/json"},
                body=body,
                access_key=self.ak,
                secret_key=self.sk,
            )
            url = f"https://{VISUAL_HOST}/?Action={QUERY_ACTION}&Version={API_VERSION}"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, content=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        def _get_status(r: dict[str, object]) -> str:
            d = r.get("data")
            if isinstance(d, dict):
                return str(d.get("status", ""))
            return ""

        result = await poll_with_retry(
            poll_fn=_query,
            is_done=lambda r: _get_status(r) == "done",
            is_failed=lambda r: f"小云雀任务过期: {r}" if _get_status(r) in ("not_found", "expired") else None,
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
