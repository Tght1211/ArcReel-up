"""POST/GET/cancel/list 端点测试。"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import xiaoyunque_shortplay as router_mod

# ---------------------------------------------------------------------------
# DB fixtures (内存 SQLite，与 test_custom_providers_api.py 同一模式)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_engine():
    """内存 SQLite 引擎。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def app(session_factory) -> FastAPI:
    _app = FastAPI()

    async def _override_session():
        async with session_factory() as s:
            yield s

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")

    _app.include_router(router_mod.router, prefix="/api/v1", tags=["xiaoyunque"])
    return _app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_external_io(monkeypatch):
    """桩掉真实 TOS 上传 + ConfigService get_provider_config。"""

    async def fake_upload_script(file_bytes, file_name, *, ak, sk, endpoint, bucket, region):
        return f"https://tos/{file_name}"

    monkeypatch.setattr(router_mod, "_upload_script_to_tos", fake_upload_script)

    from lib.config import service as cfg_service

    async def fake_get_provider_config(self, provider):
        return {
            "access_key": "ak",
            "secret_key": "sk",
            "tos_endpoint": "tos-cn-beijing.volces.com",
            "tos_bucket": "b",
            "tos_region": "cn-beijing",
        }

    monkeypatch.setattr(
        cfg_service.ConfigService,
        "get_provider_config",
        fake_get_provider_config,
    )

    # 桩掉 runner.run 避免触发真实 HTTP
    async def _noop_run(self, run_id):
        return None

    monkeypatch.setattr("lib.xiaoyunque_shortplay.runner.XiaoyunquePipelineRunner.run", _noop_run)


def test_create_run_returns_run_id(client):
    files = {
        "script_file": (
            "test.txt",
            io.BytesIO("一个简短的剧本".encode()),
            "text/plain",
        )
    }
    data = {
        "project_name": "p1",
        "visual_style": "真人写实",
        "video_ratio": "16:9",
        "model_variant": "fast720p",
    }
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs", data=data, files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "pending"


def test_create_run_rejects_too_long_script(client):
    long_text = "字" * 500
    files = {"script_file": ("big.txt", io.BytesIO(long_text.encode("utf-8")), "text/plain")}
    data = {
        "project_name": "p1",
        "visual_style": "x",
        "video_ratio": "16:9",
        "model_variant": "fast720p",
    }
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs", data=data, files=files)
    assert resp.status_code == 400


def test_create_run_rejects_unsupported_extension(client):
    files = {"script_file": ("bad.exe", io.BytesIO(b"x"), "application/x-msdownload")}
    data = {
        "project_name": "p1",
        "visual_style": "x",
        "video_ratio": "16:9",
        "model_variant": "fast720p",
    }
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs", data=data, files=files)
    assert resp.status_code == 400


def test_get_run_returns_404_for_unknown(client):
    resp = client.get("/api/v1/xiaoyunque-shortplay/runs/does-not-exist")
    assert resp.status_code == 404


def test_cancel_unknown_run_404(client):
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs/does-not-exist/cancel")
    assert resp.status_code == 404


def test_list_runs_filters_by_project(client):
    # 先建一个 run
    files = {"script_file": ("a.txt", io.BytesIO(b"short"), "text/plain")}
    data = {
        "project_name": "p_listing",
        "visual_style": "x",
        "video_ratio": "16:9",
        "model_variant": "fast720p",
    }
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs", data=data, files=files)
    assert resp.status_code == 200

    resp = client.get("/api/v1/xiaoyunque-shortplay/runs?project_name=p_listing")
    assert resp.status_code == 200
    body = resp.json()
    assert any(r["project_name"] == "p_listing" for r in body["runs"])
