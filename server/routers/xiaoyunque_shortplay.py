"""短剧 pipeline REST API。"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db import async_session_factory, get_async_session
from lib.i18n import Translator
from lib.volc_tos_uploader import TosImageUploader
from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.state import state_from_dict
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from server.auth import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter()

SCRIPT_CHAR_LIMIT = 300  # 文档限制
ALLOWED_SCRIPT_EXTS = {".docx", ".txt", ".pdf"}


async def _upload_script_to_tos(
    file_bytes: bytes,
    file_name: str,
    *,
    ak: str,
    sk: str,
    endpoint: str,
    bucket: str,
    region: str,
) -> str:
    """走 TosImageUploader（任意二进制都行）取签名 URL。"""
    suffix = "." + file_name.rsplit(".", 1)[-1] if "." in file_name else ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = Path(f.name)
    try:
        tmp.write_bytes(file_bytes)
        uploader = TosImageUploader(
            ak=ak,
            sk=sk,
            endpoint=endpoint,
            bucket=bucket,
            region=region,
        )
        url = await uploader.upload_image(tmp, key_prefix="arcreel-shortplay/")
        return url
    finally:
        tmp.unlink(missing_ok=True)


async def _fire_and_forget_runner(run_id: str, cfg: dict[str, str], model_variant: str) -> None:
    """启动后台 runner task；用真实 AK/SK 构造 client。"""

    async def _go() -> None:
        async with async_session_factory() as session:
            store = XiaoyunqueRunStore(session)
            client = XiaoyunqueShortplayClient(
                access_key=cfg["access_key"],
                secret_key=cfg["secret_key"],
                model_variant=model_variant,  # type: ignore[arg-type]
            )
            runner = XiaoyunquePipelineRunner(client, store)
            try:
                await runner.run(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("xiaoyunque runner task for run %s 异常", run_id)

    asyncio.create_task(_go(), name=f"xiaoyunque-run-{run_id}")


@router.post("/xiaoyunque-shortplay/runs")
async def create_run(
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
    project_name: str = Form(...),
    visual_style: str = Form(...),
    video_ratio: str = Form("16:9"),
    model_variant: str = Form("fast720p"),
    script_file: UploadFile = File(...),
) -> dict[str, Any]:
    # 1. 扩展名校验
    filename = script_file.filename or "script.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_SCRIPT_EXTS:
        raise HTTPException(
            status_code=400,
            detail=_t(
                "external_script_unsupported_format",
                ext=ext,
                allowed=", ".join(sorted(ALLOWED_SCRIPT_EXTS)),
            ),
        )

    # 2. 读取内容
    payload = await script_file.read()

    # 3. 字符数校验（仅对 .txt 做精细校验；docx/pdf 留给 API 端校验）
    if ext == ".txt":
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail=_t("invalid_encoding")) from exc
        if len(text) > SCRIPT_CHAR_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=_t("external_script_too_long", actual=len(text), limit=SCRIPT_CHAR_LIMIT),
            )

    # 4. 拿 provider 配置
    cfg_service = ConfigService(session)
    try:
        cfg = await cfg_service.get_provider_config("volc-xiaoyunque")
    except Exception as exc:
        logger.exception("provider 配置读取失败")
        raise HTTPException(
            status_code=400,
            detail=_t("provider_not_configured", provider="volc-xiaoyunque"),
        ) from exc

    required = ("access_key", "secret_key", "tos_endpoint", "tos_bucket", "tos_region")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=_t("provider_missing_keys", provider="volc-xiaoyunque", keys=", ".join(missing)),
        )

    # 5. 上传到 TOS
    try:
        script_url = await _upload_script_to_tos(
            payload,
            filename,
            ak=cfg["access_key"],
            sk=cfg["secret_key"],
            endpoint=cfg["tos_endpoint"],
            bucket=cfg["tos_bucket"],
            region=cfg["tos_region"],
        )
    except Exception as exc:
        logger.exception("TOS 上传失败")
        raise HTTPException(status_code=500, detail=_t("tos_upload_failed", reason=str(exc))) from exc

    # 6. 写库
    store = XiaoyunqueRunStore(session)
    run = await store.create(
        project_name=project_name,
        model_variant=model_variant,
        visual_style=visual_style,
        video_ratio=video_ratio,
        script_file_url=script_url,
    )

    # 7. fire-and-forget 启动 runner
    await _fire_and_forget_runner(run.id, cfg, model_variant)

    return {"run_id": run.id, "status": run.status}


@router.get("/xiaoyunque-shortplay/runs/{run_id}")
async def get_run(
    run_id: str,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    store = XiaoyunqueRunStore(session)
    try:
        run = await store.get(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_t("run_not_found", run_id=run_id)) from exc
    state = await store.get_state(run_id)
    return _serialize_run(run, state)


@router.get("/xiaoyunque-shortplay/runs")
async def list_runs(
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
    project_name: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    store = XiaoyunqueRunStore(session)
    if project_name:
        rows = await store.list_by_project(project_name, status=status)
    elif status is None:
        rows = await store.list_in_progress()
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for r in rows:
        state = state_from_dict(_safe_json(r.state_json))
        out.append(_serialize_run(r, state))
    return {"runs": out}


@router.post("/xiaoyunque-shortplay/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    store = XiaoyunqueRunStore(session)
    try:
        run = await store.get(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=_t("run_not_found", run_id=run_id)) from exc
    if run.status in ("done", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=_t("run_already_terminal", status=run.status),
        )
    await store.set_status(run_id, "cancelled", completed=True)
    return {"run_id": run_id, "status": "cancelled"}


def _safe_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _serialize_run(run: Any, state: Any) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "project_name": run.project_name,
        "status": run.status,
        "model_variant": run.model_variant,
        "visual_style": run.visual_style,
        "video_ratio": run.video_ratio,
        "thread_id": run.thread_id,
        "assets_id": run.assets_id,
        "script_file_url": run.script_file_url,
        "state": asdict(state),
        "last_error": run.last_error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
