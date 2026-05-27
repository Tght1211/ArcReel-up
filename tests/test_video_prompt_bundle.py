"""resolve_video_prompt_bundle 高层 API 测试。"""

from __future__ import annotations

import json

import pytest

from server.services.video_prompt_bundle import (
    VideoPromptBundle,
    resolve_video_prompt_bundle,
)


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """构造一个最小可用项目：1 个剧集 + 1 个 scene，含 char/scene sheet 和首帧。"""
    project_root = tmp_path / "fakeproject"
    project_root.mkdir()
    (project_root / "characters").mkdir()
    (project_root / "characters" / "lilei.png").write_bytes(b"c")
    (project_root / "scenes").mkdir()
    (project_root / "scenes" / "classroom.png").write_bytes(b"s")
    (project_root / "storyboards").mkdir()
    (project_root / "storyboards" / "scene_001.png").write_bytes(b"f")
    project_data = {
        "name": "fakeproject",
        "characters": {"李雷": {"character_sheet": "characters/lilei.png"}},
        "scenes": {"教室": {"scene_sheet": "scenes/classroom.png"}},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    (project_root / "project.json").write_text(json.dumps(project_data), encoding="utf-8")

    script = {
        "episode": 1,
        "scenes": [
            {
                "scene_id": "001",
                "action": "李雷走进教室",
                "camera_motion": "推镜",
                "characters_in_scene": ["李雷"],
                "scenes": ["教室"],
                "props": [],
            }
        ],
    }
    (project_root / "ep1.json").write_text(json.dumps(script), encoding="utf-8")

    from lib import project_manager as pm_mod

    monkeypatch.setattr(pm_mod.ProjectManager, "get_project_path", lambda self, name: project_root)

    return project_root


async def test_bundle_contains_prompt_and_all_refs(fake_project):
    bundle = await resolve_video_prompt_bundle("fakeproject", episode=1, segment_id="001")

    assert isinstance(bundle, VideoPromptBundle)
    assert bundle.shot_id == "001"
    assert "李雷走进教室" in bundle.prompt
    kinds = [r.kind for r in bundle.reference_images]
    assert "character_sheet" in kinds
    assert "scene_sheet" in kinds
    assert "start_image" in kinds


async def test_url_uses_files_endpoint(fake_project):
    bundle = await resolve_video_prompt_bundle("fakeproject", episode=1, segment_id="001")
    for ref in bundle.reference_images:
        assert ref.url.startswith("/api/v1/files/fakeproject/")


async def test_missing_segment_raises(fake_project):
    with pytest.raises(LookupError):
        await resolve_video_prompt_bundle("fakeproject", episode=1, segment_id="999")


async def test_missing_ref_files_skipped(fake_project, tmp_path):
    (fake_project / "characters" / "lilei.png").unlink()
    bundle = await resolve_video_prompt_bundle("fakeproject", episode=1, segment_id="001")
    kinds = [r.kind for r in bundle.reference_images]
    assert "character_sheet" not in kinds
    assert "scene_sheet" in kinds
