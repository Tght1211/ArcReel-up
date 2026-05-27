"""分镜级动作路由：复制 prompt（后续会加导入外部视频等）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from lib.i18n import Translator
from server.auth import CurrentUser
from server.services.video_prompt_bundle import (
    VideoPromptBundle,
    resolve_video_prompt_bundle,
)

logger = logging.getLogger(__name__)
router = APIRouter()


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
