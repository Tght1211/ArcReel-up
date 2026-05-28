"""一次性活体烟雾：用 ENV 传 AK/SK + TOS bucket，跑完整 4 步 pipeline。

用法：
    VOLC_AK=... VOLC_SK=... TOS_BUCKET=... TOS_REGION=cn-beijing \\
        TOS_ENDPOINT=tos-cn-beijing.volces.com \\
        uv run python scripts/xiaoyunque_shortplay_smoke.py path/to/script.txt
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smoke")


async def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from lib.volc_tos_uploader import TosImageUploader
    from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
    from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
    from lib.xiaoyunque_shortplay.state import RunState

    if len(sys.argv) < 2:
        logger.error("usage: %s <script-file>", sys.argv[0])
        return 2

    script_path = Path(sys.argv[1])
    if not script_path.exists():
        logger.error("script file not found: %s", script_path)
        return 2

    ak = os.environ.get("VOLC_AK") or ""
    sk = os.environ.get("VOLC_SK") or ""
    tos_endpoint = os.environ.get("TOS_ENDPOINT") or ""
    tos_bucket = os.environ.get("TOS_BUCKET") or ""
    tos_region = os.environ.get("TOS_REGION") or ""
    missing = [
        k
        for k, v in [
            ("VOLC_AK", ak),
            ("VOLC_SK", sk),
            ("TOS_ENDPOINT", tos_endpoint),
            ("TOS_BUCKET", tos_bucket),
            ("TOS_REGION", tos_region),
        ]
        if not v
    ]
    if missing:
        logger.error("缺失环境变量: %s", missing)
        return 2

    # 1. 上传剧本到 TOS
    uploader = TosImageUploader(
        ak=ak,
        sk=sk,
        endpoint=tos_endpoint,
        bucket=tos_bucket,
        region=tos_region,
    )
    logger.info("上传剧本到 TOS...")
    script_url = await uploader.upload_image(script_path, key_prefix="arcreel-shortplay/")
    logger.info("剧本 URL: %s...", script_url[:120])

    # 2. 直接用 client 单跑 4 步（不走 store/worker，使用内存 store 替身）
    client = XiaoyunqueShortplayClient(access_key=ak, secret_key=sk, model_variant="fast720p")

    class MemStore:
        def __init__(self) -> None:
            self._runs: dict[str, dict] = {}
            self._states: dict[str, RunState] = {}

        async def get(self, run_id: str):
            class _Run:
                pass

            r = self._runs[run_id]
            obj = _Run()
            for k, v in r.items():
                setattr(obj, k, v)
            return obj

        async def get_state(self, run_id: str) -> RunState:
            return self._states.get(run_id, RunState())

        async def set_state(self, run_id: str, s: RunState) -> None:
            self._states[run_id] = s

        async def set_status(self, run_id: str, status: str, completed: bool = False) -> None:
            self._runs[run_id]["status"] = status

        async def set_failed(self, run_id: str, err: str) -> None:
            self._runs[run_id]["status"] = "failed"
            self._runs[run_id]["last_error"] = err

        async def set_thread_and_assets(
            self,
            run_id: str,
            *,
            thread_id: str,
            assets_id: str,
        ) -> None:
            self._runs[run_id]["thread_id"] = thread_id
            self._runs[run_id]["assets_id"] = assets_id

    store = MemStore()
    run_id = "smoke-run"
    store._runs[run_id] = {
        "id": run_id,
        "project_name": "smoke",
        "status": "pending",
        "model_variant": "fast720p",
        "visual_style": "真人写实",
        "video_ratio": "16:9",
        "script_file_url": script_url,
        "thread_id": None,
        "assets_id": None,
        "last_error": None,
    }

    runner = XiaoyunquePipelineRunner(client, store)  # type: ignore[arg-type]
    start = time.monotonic()
    await runner.run(run_id)
    elapsed = time.monotonic() - start

    final = await store.get(run_id)
    state = await store.get_state(run_id)
    logger.info("[+%ds] status=%s", int(elapsed), final.status)
    for ep in state.episodes:
        logger.info("  ep %s: status=%s video=%s", ep.episode_id, ep.status, ep.final_video_url)
    return 0 if final.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
