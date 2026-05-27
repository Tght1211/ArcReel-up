"""分镜级动作路由：复制 prompt（后续会加导入外部视频等）。"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from lib.app_data_dir import app_data_dir
from lib.i18n import Translator
from lib.project_manager import ProjectManager
from server.auth import CurrentUser
from server.services.video_prompt_bundle import (
    VideoPromptBundle,
    resolve_video_prompt_bundle,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_pm = ProjectManager(app_data_dir())


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
