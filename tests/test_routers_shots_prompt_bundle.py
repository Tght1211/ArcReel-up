"""GET /api/v1/projects/{p}/episodes/{e}/shots/{s}/video-prompt-bundle"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import shots as shots_router


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """构造最小项目（与 test_video_prompt_bundle.py 共享思路）。"""
    project_root = tmp_path / "p1"
    project_root.mkdir()
    (project_root / "characters").mkdir()
    (project_root / "characters" / "a.png").write_bytes(b"c")
    project_data = {
        "name": "p1",
        "characters": {"A": {"character_sheet": "characters/a.png"}},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    (project_root / "project.json").write_text(json.dumps(project_data), encoding="utf-8")
    script = {
        "episode": 1,
        "scenes": [
            {
                "scene_id": "001",
                "action": "A 进场",
                "camera_motion": "推镜",
                "characters_in_scene": ["A"],
                "scenes": [],
                "props": [],
            }
        ],
    }
    (project_root / "ep1.json").write_text(json.dumps(script), encoding="utf-8")

    from lib import project_manager as pm_mod

    monkeypatch.setattr(pm_mod.ProjectManager, "get_project_path", lambda self, name: project_root)
    return project_root


@pytest.fixture
def client(fake_project):
    """绑定 shots router + auth override 的 TestClient。"""
    app = FastAPI()
    app.include_router(shots_router.router, prefix="/api/v1", tags=["shots"])
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    return TestClient(app)


def test_returns_bundle_json(client):
    resp = client.get("/api/v1/projects/p1/episodes/1/shots/001/video-prompt-bundle")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shot_id"] == "001"
    assert "A 进场" in body["prompt"]
    assert isinstance(body["reference_images"], list)
    assert any(r["kind"] == "character_sheet" for r in body["reference_images"])


def test_missing_segment_404(client):
    resp = client.get("/api/v1/projects/p1/episodes/1/shots/9999/video-prompt-bundle")
    assert resp.status_code == 404
