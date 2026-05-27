"""video prompt bundle 服务：把项目 + episode + segment 解析成可分发到外部的 prompt 包。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lib.app_data_dir import app_data_dir
from lib.project_manager import ProjectManager
from lib.video_prompt_resolver import collect_reference_images, normalize_video_prompt

ReferenceKind = Literal[
    "character_sheet",
    "scene_sheet",
    "prop_sheet",
    "start_image",
    "end_image",
    "previous_storyboard",
    "extra",
]


@dataclass
class VideoReferenceImage:
    kind: ReferenceKind
    label: str
    relative_path: str
    url: str
    filename: str


@dataclass
class VideoPromptBundle:
    shot_id: str
    prompt: str
    reference_images: list[VideoReferenceImage] = field(default_factory=list)
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"


_pm = ProjectManager(app_data_dir())


def _file_url(project_name: str, relative_path: str) -> str:
    return f"/api/v1/files/{project_name}/{relative_path}"


def _load_script_for_episode(project_root: Path, project_data: dict, episode: int) -> dict | None:
    for ep in project_data.get("episodes", []):
        if ep.get("episode") == episode:
            script_file = ep.get("script_file")
            if not script_file:
                return None
            script_path = project_root / script_file
            if not script_path.exists():
                return None
            return json.loads(script_path.read_text(encoding="utf-8"))
    return None


def _find_segment(script: dict, segment_id: str) -> dict | None:
    for key in ("scenes", "segments"):
        for item in script.get(key, []):
            if str(item.get("scene_id") or item.get("segment_id")) == str(segment_id):
                return item
    return None


def _build_references(
    project_name: str,
    project_root: Path,
    project_data: dict,
    segment: dict,
) -> list[VideoReferenceImage]:
    refs: list[VideoReferenceImage] = []

    target_item = {
        "characters": segment.get("characters_in_scene", []) or segment.get("characters_in_segment", []),
        "scenes": segment.get("scenes", []),
        "props": segment.get("props", []),
    }
    raw_refs = (
        collect_reference_images(
            project_data,
            project_root,
            target_item,
            char_field="characters",
            scene_field="scenes",
            prop_field="props",
        )
        or []
    )

    char_sheets = {
        (project_root / v.get("character_sheet", "")).resolve(): name
        for name, v in project_data.get("characters", {}).items()
        if v.get("character_sheet")
    }
    scene_sheets = {
        (project_root / v.get("scene_sheet", "")).resolve(): name
        for name, v in project_data.get("scenes", {}).items()
        if v.get("scene_sheet")
    }
    prop_sheets = {
        (project_root / v.get("prop_sheet", "")).resolve(): name
        for name, v in project_data.get("props", {}).items()
        if v.get("prop_sheet")
    }

    for ref in raw_refs:
        if not isinstance(ref, Path):
            continue
        rel = ref.relative_to(project_root).as_posix()
        resolved = ref.resolve()
        if resolved in char_sheets:
            kind: ReferenceKind = "character_sheet"
            label = char_sheets[resolved]
        elif resolved in scene_sheets:
            kind = "scene_sheet"
            label = scene_sheets[resolved]
        elif resolved in prop_sheets:
            kind = "prop_sheet"
            label = prop_sheets[resolved]
        else:
            kind = "extra"
            label = ref.name
        refs.append(
            VideoReferenceImage(
                kind=kind,
                label=label,
                relative_path=rel,
                filename=ref.name,
                url=_file_url(project_name, rel),
            )
        )

    storyboard = project_root / "storyboards" / f"scene_{segment.get('scene_id') or segment.get('segment_id')}.png"
    if storyboard.exists():
        rel = storyboard.relative_to(project_root).as_posix()
        refs.append(
            VideoReferenceImage(
                kind="start_image",
                label="首帧",
                relative_path=rel,
                filename=storyboard.name,
                url=_file_url(project_name, rel),
            )
        )

    return refs


async def resolve_video_prompt_bundle(
    project_name: str,
    episode: int,
    segment_id: str,
) -> VideoPromptBundle:
    def _sync() -> VideoPromptBundle:
        project_root = _pm.get_project_path(project_name)
        project_data = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        script = _load_script_for_episode(project_root, project_data, episode)
        if not script:
            raise LookupError(f"episode {episode} not found")
        segment = _find_segment(script, segment_id)
        if not segment:
            raise LookupError(f"segment {segment_id} not found in episode {episode}")

        structured = {
            "action": segment.get("action") or segment.get("text") or "",
            "camera_motion": segment.get("camera_motion") or "",
            "ambiance_audio": segment.get("ambiance_audio") or "",
            "dialogue": segment.get("dialogue") or [],
        }
        try:
            prompt = normalize_video_prompt(structured)
        except ValueError:
            text = segment.get("text") or segment.get("narration") or ""
            if not text.strip():
                raise
            prompt = normalize_video_prompt(text)

        refs = _build_references(project_name, project_root, project_data, segment)
        return VideoPromptBundle(
            shot_id=str(segment.get("scene_id") or segment.get("segment_id") or segment_id),
            prompt=prompt,
            reference_images=refs,
            duration_seconds=int(segment.get("duration_seconds") or 5),
            aspect_ratio=segment.get("aspect_ratio") or "9:16",
        )

    return await asyncio.to_thread(_sync)
