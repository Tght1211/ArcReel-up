"""视频生成 prompt 与参考图收集 — 与 generation_tasks worker 共享的纯函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.prompt_builders import append_video_negative_tail
from lib.prompt_utils import is_structured_video_prompt, video_prompt_to_yaml
from lib.storyboard_sequence import build_previous_storyboard_reference


def normalize_video_prompt(prompt: str | dict) -> str:
    """归一化视频 prompt 并在末尾追加统一文本化的反向提示词。"""
    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        return append_video_negative_tail(prompt)

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_video_prompt(prompt):
        raise ValueError("prompt must be a string or include action/camera_motion")

    action_text = str(prompt.get("action", "")).strip()
    if not action_text:
        raise ValueError("prompt.action must not be empty")

    dialogue = prompt.get("dialogue", [])
    if dialogue is None:
        dialogue = []
    if not isinstance(dialogue, list):
        raise ValueError("prompt.dialogue must be an array")

    normalized_dialogue = []
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or "").strip()
        line = str(item.get("line", "") or "").strip()
        if speaker or line:
            normalized_dialogue.append({"speaker": speaker, "line": line})

    normalized_prompt: dict[str, Any] = {
        "action": action_text,
        "camera_motion": str(prompt.get("camera_motion", "") or "") or "Static",
        "ambiance_audio": str(prompt.get("ambiance_audio", "") or ""),
        "dialogue": normalized_dialogue,
    }
    return append_video_negative_tail(video_prompt_to_yaml(normalized_prompt))


def collect_sheet_paths(
    project: dict,
    project_path: Path,
    items: list[dict],
    *,
    char_field: str,
    scene_field: str,
    prop_field: str,
    max_count: int = 0,
) -> tuple[list[Path], set[str]]:
    """从 scene/segment 收集 character_sheet/scene_sheet/prop_sheet 路径。

    Returns (list of existing Paths, set of relative sheet strings for dedup).
    If *max_count* > 0 collection stops after that many images.
    """
    seen: set[str] = set()
    paths: list[Path] = []

    characters = project.get("characters", {})
    project_scenes = project.get("scenes", {})
    project_props = project.get("props", {})

    for item in items:
        for char_name in item.get(char_field, []):
            sheet = characters.get(char_name, {}).get("character_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        for scene_name in item.get(scene_field, []):
            sheet = project_scenes.get(scene_name, {}).get("scene_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        for prop_name in item.get(prop_field, []):
            sheet = project_props.get(prop_name, {}).get("prop_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        if max_count and len(paths) >= max_count:
            break

    return paths, seen


def collect_reference_images(
    project: dict,
    project_path: Path,
    target_item: dict,
    *,
    char_field: str,
    scene_field: str,
    prop_field: str,
    extra_reference_images: list[str] | None = None,
    previous_storyboard_path: Path | None = None,
) -> list[object] | None:
    """收集参考图列表（character/scene/prop sheet + extra + previous storyboard）。"""
    sheet_paths, _ = collect_sheet_paths(
        project,
        project_path,
        [target_item],
        char_field=char_field,
        scene_field=scene_field,
        prop_field=prop_field,
    )
    reference_images: list[object] = list(sheet_paths)

    for extra in extra_reference_images or []:
        extra_path = Path(extra)
        if not extra_path.is_absolute():
            extra_path = project_path / extra_path
        if extra_path.exists():
            reference_images.append(extra_path)

    if previous_storyboard_path and previous_storyboard_path.exists():
        reference_images.append(build_previous_storyboard_reference(previous_storyboard_path))

    return reference_images or None
