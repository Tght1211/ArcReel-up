"""4 步 API 薄壳封装，走官方 volcengine.visual.VisualService。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from volcengine.visual.VisualService import VisualService

from lib.xiaoyunque_shortplay.errors import XiaoyunqueAPIError

logger = logging.getLogger(__name__)

ModelVariant = Literal["fast720p", "pro720p"]

_VIDEO_GEN_REQ_KEY = {
    "fast720p": "pippit_shortplay_cvtob_video_generate_fast720p",
    "pro720p": "pippit_shortplay_cvtob_video_generate_pro720p",
}
_VIDEO_COMPOSE_REQ_KEY = {
    "fast720p": "pippit_shortplay_cvtob_video_compose_fast720p",
    "pro720p": "pippit_shortplay_cvtob_video_compose_pro720p",
}


@dataclass
class EpisodeAssetInfo:
    episode_id: str
    episode_title: str = ""
    episode_asset_id: str = ""
    character_asset_ids: list[str] = field(default_factory=list)
    scene_asset_ids: list[str] = field(default_factory=list)


@dataclass
class ScriptAnalysisResult:
    status: str
    thread_id: str | None = None
    assets_id: str | None = None
    episode_count: int = 0
    episodes: list[EpisodeAssetInfo] = field(default_factory=list)
    charge_count: int = 0


@dataclass
class CharacterDesignInfo:
    character_id: str
    character_name: str
    body_image_url: str | None = None
    bust_portrait_url: str | None = None
    appearance_count: int = 0
    actual_render_count: int = 0


@dataclass
class SceneDesignInfo:
    scene_id: str
    name: str
    image_urls: list[str] = field(default_factory=list)


@dataclass
class MaterialDesignResult:
    status: str
    characters: list[CharacterDesignInfo] = field(default_factory=list)
    scenes: list[SceneDesignInfo] = field(default_factory=list)
    image_count: int = 0


@dataclass
class ShotInfo:
    shot_id: str
    description: str
    status: int
    video_url: str | None = None
    duration_ms: int = 0


@dataclass
class VideoGenerateResult:
    status: str
    storyboard_status_map: dict[str, int] = field(default_factory=dict)
    shots: list[ShotInfo] = field(default_factory=list)
    charge_count: int = 0


@dataclass
class VideoComposeResult:
    status: str
    final_video_url: str | None = None
    final_cover_url: str | None = None


class XiaoyunqueShortplayClient:
    """4 步短剧 pipeline 调用封装。"""

    def __init__(self, *, access_key: str, secret_key: str, model_variant: ModelVariant = "fast720p"):
        self._visual = VisualService()
        self._visual.set_ak(access_key)
        self._visual.set_sk(secret_key)
        self._model_variant: ModelVariant = model_variant

    async def submit_script_analysis(
        self,
        *,
        visual_style: str,
        video_ratio: str,
        file_url: str,
        file_type: str,
        file_name: str,
    ) -> str:
        form: dict[str, Any] = {
            "req_key": "pippit_shortplay_cvtob_script_analysis",
            "visual_style": visual_style,
            "video_ratio": video_ratio,
            "file_url": file_url,
            "file_type": file_type,
            "file_name": file_name,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_script_analysis(self, task_id: str) -> ScriptAnalysisResult:
        form = {"req_key": "pippit_shortplay_cvtob_script_analysis", "task_id": task_id}
        data = await self._query(form)
        return _parse_script_analysis(data)

    async def submit_material_design(self, *, assets_id: str, thread_id: str, run_id: str) -> str:
        form = {
            "req_key": "pippit_shortplay_cvtob_material_design",
            "assets_id": assets_id,
            "thread_id": thread_id,
            "run_id": run_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_material_design(self, task_id: str) -> MaterialDesignResult:
        form = {"req_key": "pippit_shortplay_cvtob_material_design", "task_id": task_id}
        data = await self._query(form)
        return _parse_material_design(data)

    async def submit_video_generate(
        self,
        *,
        assets_id: str,
        thread_id: str,
        episode_id: str,
        run_id: str,
    ) -> str:
        form = {
            "req_key": _VIDEO_GEN_REQ_KEY[self._model_variant],
            "assets_id": assets_id,
            "thread_id": thread_id,
            "episode_id": episode_id,
            "run_id": run_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_video_generate(self, task_id: str) -> VideoGenerateResult:
        form = {"req_key": _VIDEO_GEN_REQ_KEY[self._model_variant], "task_id": task_id}
        data = await self._query(form)
        return _parse_video_generate(data)

    async def submit_video_compose(self, *, assets_id: str, thread_id: str, episode_id: str) -> str:
        form = {
            "req_key": _VIDEO_COMPOSE_REQ_KEY[self._model_variant],
            "assets_id": assets_id,
            "thread_id": thread_id,
            "episode_id": episode_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_video_compose(self, task_id: str) -> VideoComposeResult:
        form = {"req_key": _VIDEO_COMPOSE_REQ_KEY[self._model_variant], "task_id": task_id}
        data = await self._query(form)
        return _parse_video_compose(data)

    async def _submit(self, form: dict[str, Any]) -> dict[str, Any]:
        resp = await asyncio.to_thread(self._visual.cv_sync2async_submit_task, form)
        if not isinstance(resp, dict):
            raise XiaoyunqueAPIError(code=-1, message=f"non-dict response: {resp!r}")
        if resp.get("code") != 10000:
            raise XiaoyunqueAPIError(
                code=int(resp.get("code") or -1),
                message=str(resp.get("message", "")),
                request_id=resp.get("request_id"),
            )
        return resp

    async def _query(self, form: dict[str, Any]) -> dict[str, Any]:
        resp = await asyncio.to_thread(self._visual.cv_sync2async_get_result, form)
        if not isinstance(resp, dict):
            raise XiaoyunqueAPIError(code=-1, message=f"non-dict response: {resp!r}")
        if resp.get("code") != 10000:
            raise XiaoyunqueAPIError(
                code=int(resp.get("code") or -1),
                message=str(resp.get("message", "")),
                request_id=resp.get("request_id"),
            )
        return resp

    @staticmethod
    def _extract_task_id(resp: dict[str, Any]) -> str:
        data = resp.get("data")
        if not isinstance(data, dict) or not data.get("task_id"):
            raise XiaoyunqueAPIError(code=-1, message=f"missing task_id: {resp!r}")
        return str(data["task_id"])


def _resp_data_dict(resp: dict[str, Any]) -> dict[str, Any]:
    data = resp.get("data") or {}
    raw = data.get("resp_data")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_status(resp: dict[str, Any]) -> str:
    data = resp.get("data") or {}
    return str(data.get("status", ""))


def _parse_script_analysis(resp: dict[str, Any]) -> ScriptAnalysisResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    detail = rd.get("script_detail") or {}
    episodes_raw = detail.get("EpisodeAssets") or []
    core = detail.get("CoreElement") or {}
    episode_count = int(core.get("EpisodeCount") or 0)

    episodes = [
        EpisodeAssetInfo(
            episode_id=str(e.get("EpisodeID", "")),
            episode_title=str(e.get("EpisodeTitle", "")),
            episode_asset_id=str(e.get("EpisodeAssetID", "")),
            character_asset_ids=[str(x) for x in (e.get("CharacterAssetIDs") or [])],
            scene_asset_ids=[str(x) for x in (e.get("SceneAssetIDs") or [])],
        )
        for e in episodes_raw
    ]
    return ScriptAnalysisResult(
        status=status,
        thread_id=rd.get("thread_id"),
        assets_id=rd.get("assets_id"),
        episode_count=episode_count,
        episodes=episodes,
        charge_count=int(rd.get("charge_count") or 0),
    )


def _parse_material_design(resp: dict[str, Any]) -> MaterialDesignResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)

    chars: list[CharacterDesignInfo] = []
    for c in rd.get("character_detail") or []:
        tree = c.get("AppearanceTree") or {}
        detail = tree.get("Detail") or {}
        chars.append(
            CharacterDesignInfo(
                character_id=str(c.get("CharacterID", "")),
                character_name=str(c.get("CharacterName", "")),
                body_image_url=detail.get("BodyImageURL"),
                bust_portrait_url=detail.get("BustPortraitURL"),
                appearance_count=int(c.get("ExpectRenderImageCount") or 0),
                actual_render_count=int(c.get("ActualRenderImageCount") or 0),
            )
        )

    scenes: list[SceneDesignInfo] = []
    for s in rd.get("scene_detail") or []:
        urls = [ap.get("ImageURL") for ap in (s.get("AppearanceDetails") or []) if ap.get("ImageURL")]
        scenes.append(
            SceneDesignInfo(
                scene_id=str(s.get("SceneID", "")),
                name=str(s.get("Name", "")),
                image_urls=[u for u in urls if u],
            )
        )

    return MaterialDesignResult(
        status=status,
        characters=chars,
        scenes=scenes,
        image_count=int(rd.get("image_count") or 0),
    )


def _parse_video_generate(resp: dict[str, Any]) -> VideoGenerateResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    storyboard = (rd.get("storyboard_detail") or [{}])[0] if rd.get("storyboard_detail") else {}

    status_map_raw = storyboard.get("ShotStatusMap") or {}
    status_map = {shot_id: int((info or {}).get("Status") or 0) for shot_id, info in status_map_raw.items()}

    shots = [
        ShotInfo(
            shot_id=str(s.get("ShotID", "")),
            description=str(s.get("Description", "")),
            status=int(s.get("Status") or 0),
            video_url=s.get("VideoURL"),
            duration_ms=int(s.get("Duration") or 0),
        )
        for s in storyboard.get("Shots") or []
    ]
    return VideoGenerateResult(
        status=status,
        storyboard_status_map=status_map,
        shots=shots,
        charge_count=int(rd.get("charge_count") or 0),
    )


def _parse_video_compose(resp: dict[str, Any]) -> VideoComposeResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    return VideoComposeResult(
        status=status,
        final_video_url=rd.get("final_video_url"),
        final_cover_url=rd.get("final_cover_url"),
    )
