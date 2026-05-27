"""POST /api/v1/.../shots/{id}/import-video"""

from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import shots as shots_router


def _make_fake_mp4(size_bytes: int = 1024) -> bytes:
    return b"\x00\x00\x00\x1cftypisom" + b"\x00" * (size_bytes - 12)


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    project_root = tmp_path / "p1"
    project_root.mkdir()
    project_data = {
        "name": "p1",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
        "content_mode": "drama",
    }
    (project_root / "project.json").write_text(json.dumps(project_data), encoding="utf-8")
    script = {
        "episode": 1,
        "content_mode": "drama",
        "scenes": [{"scene_id": "001", "action": "x", "characters_in_scene": [], "scenes": [], "props": []}],
    }
    # load_script 从 scripts/ 子目录读取，update_scene_asset 内部会调用 load_script
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ep1.json").write_text(json.dumps(script), encoding="utf-8")

    from lib import project_manager as pm_mod

    monkeypatch.setattr(pm_mod.ProjectManager, "get_project_path", lambda self, name: project_root)
    return project_root


@pytest.fixture
def client(fake_project, monkeypatch):
    # 让 ffmpeg 转换 / 缩略图都走 mock，避免依赖真实 ffmpeg
    async def fake_convert(src, dst):
        # 模拟 mov/webm → mp4：直接 copy/rename src 到 dst
        dst.write_bytes(src.read_bytes())

    async def fake_thumb(video_path, thumb_path):
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(b"\xff\xd8\xff\xe0")  # 假 JPEG header
        return thumb_path

    monkeypatch.setattr(shots_router, "_convert_to_mp4_if_needed", fake_convert)
    from lib import thumbnail as thumb_mod

    monkeypatch.setattr(thumb_mod, "extract_video_thumbnail", fake_thumb)
    # 同时 patch shots_router 里的导入引用
    monkeypatch.setattr(shots_router, "extract_video_thumbnail", fake_thumb)

    app = FastAPI()
    app.include_router(shots_router.router, prefix="/api/v1", tags=["shots"])
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    return TestClient(app)


def test_import_mp4_success(client):
    payload = _make_fake_mp4(2048)
    resp = client.post(
        "/api/v1/projects/p1/episodes/1/shots/001/import-video",
        files={"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["video_path"] == "videos/scene_001.mp4"
    assert body["version"] >= 1


def test_unsupported_extension_400(client):
    resp = client.post(
        "/api/v1/projects/p1/episodes/1/shots/001/import-video",
        files={"file": ("clip.avi", io.BytesIO(b"x"), "video/x-msvideo")},
    )
    assert resp.status_code == 400


def test_oversize_413(client):
    huge = b"x" * (201 * 1024 * 1024)
    resp = client.post(
        "/api/v1/projects/p1/episodes/1/shots/001/import-video",
        files={"file": ("big.mp4", io.BytesIO(huge), "video/mp4")},
    )
    assert resp.status_code == 413


def test_episode_not_found_404(client):
    payload = _make_fake_mp4()
    resp = client.post(
        "/api/v1/projects/p1/episodes/999/shots/001/import-video",
        files={"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")},
    )
    assert resp.status_code == 404
