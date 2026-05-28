"""短剧 pipeline 运行时状态 dataclass + JSON 序列化往返。

state_json 持久化在 xiaoyunque_runs 表的 JSON 列；老版本字段缺失要兼容。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EpisodeStatus = Literal["pending", "generating", "composing", "done", "failed", "failed_compose"]


@dataclass
class ScriptSummary:
    title: str = ""
    episode_count: int = 0
    core_setting: str = ""


@dataclass
class CharacterState:
    character_id: str
    name: str
    body_image_url: str | None = None
    bust_portrait_url: str | None = None
    appearance_count: int = 0


@dataclass
class SceneState:
    scene_id: str
    name: str
    image_urls: list[str] = field(default_factory=list)


@dataclass
class ShotState:
    shot_id: str
    description: str = ""
    status: int = 0  # 火山 Shot Status: 0/1/2/3/4/5
    video_url: str | None = None
    duration_ms: int = 0


@dataclass
class EpisodeState:
    episode_id: str
    episode_asset_id: str = ""
    title: str = ""
    status: EpisodeStatus = "pending"
    shots: list[ShotState] = field(default_factory=list)
    video_generate_task_id: str | None = None
    video_compose_task_id: str | None = None
    final_video_url: str | None = None
    final_cover_url: str | None = None
    error_message: str | None = None


@dataclass
class RunState:
    parse_task_id: str | None = None
    design_task_id: str | None = None
    script: ScriptSummary | None = None
    characters: list[CharacterState] = field(default_factory=list)
    scenes: list[SceneState] = field(default_factory=list)
    episodes: list[EpisodeState] = field(default_factory=list)
    charge_count_total: int = 0


def state_to_dict(s: RunState) -> dict[str, Any]:
    return asdict(s)


def state_from_dict(d: dict[str, Any]) -> RunState:
    """兼容旧版本字段缺失。"""
    if d is None:
        return RunState()
    script_d = d.get("script")
    script = ScriptSummary(**script_d) if isinstance(script_d, dict) else None
    return RunState(
        parse_task_id=d.get("parse_task_id"),
        design_task_id=d.get("design_task_id"),
        script=script,
        characters=[CharacterState(**c) for c in d.get("characters") or []],
        scenes=[SceneState(**s) for s in d.get("scenes") or []],
        episodes=[
            EpisodeState(
                episode_id=e.get("episode_id", ""),
                episode_asset_id=e.get("episode_asset_id", ""),
                title=e.get("title", ""),
                status=e.get("status", "pending"),
                shots=[ShotState(**sh) for sh in e.get("shots") or []],
                video_generate_task_id=e.get("video_generate_task_id"),
                video_compose_task_id=e.get("video_compose_task_id"),
                final_video_url=e.get("final_video_url"),
                final_cover_url=e.get("final_cover_url"),
                error_message=e.get("error_message"),
            )
            for e in d.get("episodes") or []
        ],
        charge_count_total=int(d.get("charge_count_total") or 0),
    )
