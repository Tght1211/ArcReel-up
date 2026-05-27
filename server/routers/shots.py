"""分镜级动作路由：复制 prompt（后续会加导入外部视频等）。"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from lib.app_data_dir import app_data_dir
from lib.i18n import Translator
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager
from server.auth import CurrentUser
from server.services.video_prompt_bundle import (
    VideoPromptBundle,
    resolve_video_prompt_bundle,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_pm = ProjectManager(app_data_dir())

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
MAX_VIDEO_SIZE = 200 * 1024 * 1024  # 200 MiB


async def _convert_to_mp4_if_needed(src: Path, dst: Path) -> None:
    """容器转换：mov/webm → mp4。优先 stream copy，失败兜底重编码。"""
    if src.suffix.lower() == ".mp4":
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_required")

    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        str(dst),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, _err = await proc.communicate()
    if proc.returncode == 0:
        return

    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(dst),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', 'ignore')[:200]}")


def _resolve_script_filename(project_data: dict, episode: int) -> str | None:
    for ep in project_data.get("episodes", []):
        if ep.get("episode") == episode:
            sf = ep.get("script_file")
            return sf if isinstance(sf, str) else None
    return None


@router.get("/projects/{project_name}/episodes/{episode}/shots/{segment_id}/video-prompt-bundle")
async def get_video_prompt_bundle(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
) -> dict:
    try:
        bundle = await resolve_video_prompt_bundle(project_name, episode, segment_id)
    except LookupError:
        raise HTTPException(status_code=404, detail=_t("video_prompt_bundle_not_ready"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _bundle_to_dict(bundle)


def _bundle_to_dict(b: VideoPromptBundle) -> dict:
    return {
        "shot_id": b.shot_id,
        "prompt": b.prompt,
        "duration_seconds": b.duration_seconds,
        "aspect_ratio": b.aspect_ratio,
        "reference_images": [
            {
                "kind": r.kind,
                "label": r.label,
                "url": r.url,
                "filename": r.filename,
                "relative_path": r.relative_path,
            }
            for r in b.reference_images
        ],
    }


@router.get("/projects/{project_name}/episodes/{episode}/shots/{segment_id}/video-prompt-bundle.zip")
async def get_video_prompt_bundle_zip(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
) -> StreamingResponse:
    try:
        bundle = await resolve_video_prompt_bundle(project_name, episode, segment_id)
        project_root = _pm.get_project_path(project_name)
    except LookupError:
        raise HTTPException(status_code=404, detail=_t("video_prompt_bundle_not_ready"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("prompt.txt", bundle.prompt)
        readme_lines = [
            "ArcReel video prompt bundle",
            f"Project: {project_name}",
            f"Episode: {episode}",
            f"Shot: {bundle.shot_id}",
            f"Generated at: {datetime.now(UTC).isoformat()}",
            f"Duration: {bundle.duration_seconds}s",
            f"Aspect ratio: {bundle.aspect_ratio}",
            "",
            "Files:",
            "  prompt.txt        — final prompt (含反向提示词)",
            "  references/       — 参考图，按序号排列",
        ]
        zf.writestr("README.txt", "\n".join(readme_lines) + "\n")

        for idx, ref in enumerate(bundle.reference_images, start=1):
            src = project_root / ref.relative_path
            if not src.exists():
                logger.warning("ref missing during zip: %s", ref.relative_path)
                continue
            arcname = f"references/{idx:02d}_{ref.kind}_{ref.filename}"
            zf.writestr(arcname, src.read_bytes())

    buf.seek(0)
    fname = f"shot_{bundle.shot_id}_video_bundle.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/projects/{project_name}/episodes/{episode}/shots/{segment_id}/import-video")
async def import_external_video(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
) -> dict:
    # 1. 扩展名校验
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=_t(
                "external_video_unsupported_format",
                ext=ext,
                allowed=", ".join(sorted(ALLOWED_VIDEO_EXTENSIONS)),
            ),
        )

    # 2. 读取 + 大小校验（200 MiB）
    payload = await file.read()
    if len(payload) > MAX_VIDEO_SIZE:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "external_video_too_large",
                size_mb=round(len(payload) / 1024 / 1024, 1),
                limit_mb=round(MAX_VIDEO_SIZE / 1024 / 1024, 1),
            ),
        )

    # 3. 解析 project / 找到 script_filename
    try:
        project_data = await asyncio.to_thread(_pm.load_project, project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

    script_filename = _resolve_script_filename(project_data, episode)
    if not script_filename:
        raise HTTPException(status_code=404, detail=_t("video_prompt_bundle_not_ready"))

    project_root = _pm.get_project_path(project_name)
    videos_dir = project_root / "videos"
    thumbs_dir = project_root / "thumbnails"
    videos_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    target_video = videos_dir / f"scene_{segment_id}.mp4"
    target_thumb = thumbs_dir / f"scene_{segment_id}.jpg"

    # 4. 写 tmp，再做容器转换
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_path.write_bytes(payload)
        try:
            await _convert_to_mp4_if_needed(tmp_path, target_video)
        except RuntimeError as e:
            if "ffmpeg_required" in str(e):
                raise HTTPException(status_code=500, detail=_t("ffmpeg_required"))
            raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    # 5. 缩略图（async；ffmpeg 缺失返回 None，不阻断）
    await extract_video_thumbnail(target_video, target_thumb)

    # 6. VersionManager.add_version（同步方法走线程池）
    vm = VersionManager(project_root)
    version = await asyncio.to_thread(
        vm.add_version,
        "videos",
        segment_id,
        "(imported external video)",
        target_video,
        source="external",
        original_filename=filename,
    )

    # 7. update_scene_asset（同步方法走线程池）
    await asyncio.to_thread(
        _pm.update_scene_asset,
        project_name,
        script_filename,
        segment_id,
        "video_clip",
        f"videos/scene_{segment_id}.mp4",
    )
    if target_thumb.exists():
        await asyncio.to_thread(
            _pm.update_scene_asset,
            project_name,
            script_filename,
            segment_id,
            "video_thumbnail",
            f"thumbnails/scene_{segment_id}.jpg",
        )

    # emit project event 让前端 SSE 监听者刷新该镜头的视频
    change = {
        "entity_type": "scene_asset",
        "action": "updated",
        "entity_id": segment_id,
        "label": _t("external_video_imported", segment_id=segment_id),
        "episode": episode,
        "focus": {"pane": "timeline", "episode": episode, "segment_id": segment_id},
        "important": False,
    }
    try:
        with project_change_source("webui"):
            emit_project_change_batch(project_name, [change], source="webui")
    except Exception:
        logger.warning("emit external_video event failed", exc_info=True)

    return {
        "success": True,
        "video_path": f"videos/scene_{segment_id}.mp4",
        "thumbnail_path": f"thumbnails/scene_{segment_id}.jpg",
        "version": version,
        "url": f"/api/v1/files/{project_name}/videos/scene_{segment_id}.mp4",
    }
