# 分镜 prompt 复制 + 外部视频导入 + 小云雀视频后端 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个分镜的视频卡上加"复制 prompt+参考图"和"导入外部视频"两个能力，并接入火山小云雀视频后端，让用户在本地资源不足时有逃生通道，也能用上新模型。

**Architecture:** 抽出 `video_prompt_resolver.py` 让 worker 与新端点共享 prompt 装配；新端点挂 `server/routers/shots.py`，前端在 `MediaCard` 上加两个 icon 按钮 + 两个 Modal；小云雀走 `lib/volc_visual_shared.py`（Sig v4）+ `lib/volc_tos_uploader.py`（参考图上传）+ `lib/video_backends/volc_xiaoyunque.py` 三件套，注册到 video_backends registry。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / React 19 + TS / Tailwind / Vite / ffmpeg / httpx / pytest / Volcengine 视觉服务（手实现 Sig v4）。

**Spec:** `docs/superpowers/specs/2026-05-28-shot-prompt-copy-video-import-xiaoyunque-design.md`

**Pre-implementation TODOs（这些不阻塞 Phase 0/A/B/C1-C5，但阻塞 Phase C 联调）：**
- 小云雀"无参考"接口的 `req_key` 文档
- 查询任务 Action 名（猜测 `CVSync2AsyncGetResult`，需 AK/SK 探活）
- 小云雀单价表（cost_calculator 接入用）

---

## Phase 0 · 重构：抽出 video_prompt_resolver

把 `_normalize_video_prompt` 和 `_collect_reference_images` 抽出来做成独立模块，让 worker 和 Phase A 的新端点用同一份代码，避免漂移。这一步**纯重构、不改行为**。

### Task 0.1 · 新建 `lib/video_prompt_resolver.py` 并把纯函数搬过去

**Files:**
- Create: `lib/video_prompt_resolver.py`
- Modify: `server/services/generation_tasks.py` (移除 446-486、500-576；改 import)
- Modify: `tests/test_generation_tasks_service.py:170-200` (改 import 路径)
- Create: `tests/test_video_prompt_resolver.py`

- [ ] **Step 1：写失败测试 `tests/test_video_prompt_resolver.py`**

```python
"""video_prompt_resolver 抽出后的回归测试。"""

from __future__ import annotations

import pytest

from lib.video_prompt_resolver import (
    normalize_video_prompt,
    collect_sheet_paths,
)


class TestNormalizeVideoPrompt:
    def test_string_prompt_gets_negative_tail_appended(self):
        out = normalize_video_prompt("a cat dancing")
        assert "a cat dancing" in out
        # 反向提示词追加后，原 prompt 应在头部、追加内容在尾部
        assert out.startswith("a cat dancing")
        assert len(out) > len("a cat dancing")

    def test_structured_prompt_serialized_to_yaml(self):
        out = normalize_video_prompt(
            {
                "action": "cat walks across the room",
                "camera_motion": "slow pan",
                "ambiance_audio": "soft footsteps",
                "dialogue": [{"speaker": "Cat", "line": "meow"}],
            }
        )
        assert "action:" in out
        assert "cat walks across the room" in out
        assert "camera_motion:" in out

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_video_prompt("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_video_prompt("   ")

    def test_missing_action_in_structured_raises(self):
        with pytest.raises(ValueError, match="action"):
            normalize_video_prompt({"action": ""})

    def test_invalid_dialogue_type_raises(self):
        with pytest.raises(ValueError, match="dialogue"):
            normalize_video_prompt({"action": "x", "dialogue": "not a list"})


class TestCollectSheetPaths:
    def test_returns_existing_paths_in_order(self, tmp_path):
        # 准备项目结构
        (tmp_path / "characters").mkdir()
        char_sheet = tmp_path / "characters" / "lilei.png"
        char_sheet.write_bytes(b"fake")
        (tmp_path / "scenes").mkdir()
        scene_sheet = tmp_path / "scenes" / "classroom.png"
        scene_sheet.write_bytes(b"fake")

        project = {
            "characters": {"李雷": {"character_sheet": "characters/lilei.png"}},
            "scenes": {"教室": {"scene_sheet": "scenes/classroom.png"}},
            "props": {},
        }
        items = [{"chars": ["李雷"], "scns": ["教室"], "prs": []}]

        paths, seen = collect_sheet_paths(
            project, tmp_path, items,
            char_field="chars", scene_field="scns", prop_field="prs",
        )

        assert paths == [char_sheet, scene_sheet]
        assert seen == {"characters/lilei.png", "scenes/classroom.png"}

    def test_skips_missing_files(self, tmp_path):
        project = {
            "characters": {"幽灵": {"character_sheet": "characters/ghost.png"}},
            "scenes": {},
            "props": {},
        }
        items = [{"chars": ["幽灵"], "scns": [], "prs": []}]

        paths, _ = collect_sheet_paths(
            project, tmp_path, items,
            char_field="chars", scene_field="scns", prop_field="prs",
        )

        assert paths == []
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_video_prompt_resolver.py -v
```

期望：`ModuleNotFoundError: No module named 'lib.video_prompt_resolver'`。

- [ ] **Step 3：创建 `lib/video_prompt_resolver.py`**

把 `server/services/generation_tasks.py` 的 446-486（`_normalize_video_prompt`）和 500-547（`_collect_sheet_paths`）原封不动搬过来，把前导下划线去掉（变成 public API），然后 `_collect_reference_images`（550-576）也搬过来。文件头：

```python
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
    """从 scene/segment 收集 character_sheet/scene_sheet/prop_sheet 路径。"""
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
    sheet_paths, _ = collect_sheet_paths(
        project, project_path, [target_item],
        char_field=char_field, scene_field=scene_field, prop_field=prop_field,
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
```

- [ ] **Step 4：跑测试确认通过**

```bash
uv run pytest tests/test_video_prompt_resolver.py -v
```

期望：6 passed。

- [ ] **Step 5：删除 `generation_tasks.py` 中的旧定义并改 import**

在 `server/services/generation_tasks.py` 中：

1. 删除 446-486、500-576 这三个函数定义
2. 顶部 import 区域追加：

```python
from lib.video_prompt_resolver import (
    collect_reference_images as _collect_reference_images,
    collect_sheet_paths as _collect_sheet_paths,
    normalize_video_prompt as _normalize_video_prompt,
)
```

保留 `_` 前缀别名是为了 **零调用点改动**（837、746 调用点保持原样）。删除 28-33 的 `from lib.prompt_utils import` 中已经不再直接用的 `is_structured_video_prompt`、`video_prompt_to_yaml`（如果仅在被移除函数里用过；若 storyboard 分支还在用 `image_prompt_to_yaml` / `is_structured_image_prompt` 就保留）。

- [ ] **Step 6：改老测试的 import 路径（保留覆盖率）**

`tests/test_generation_tasks_service.py:170-200` 中所有 `generation_tasks._normalize_video_prompt(...)` 调用改为：

```python
from lib import video_prompt_resolver

# ...
video_yaml = video_prompt_resolver.normalize_video_prompt(...)
```

去掉所有 `generation_tasks._normalize_video_prompt` 引用。

- [ ] **Step 7：跑完整 worker 测试 + 新 resolver 测试**

```bash
uv run pytest tests/test_generation_tasks_service.py tests/test_video_prompt_resolver.py -v
```

期望：全绿。

- [ ] **Step 8：lint + 类型检查**

```bash
uv run ruff check lib/video_prompt_resolver.py server/services/generation_tasks.py tests/test_video_prompt_resolver.py tests/test_generation_tasks_service.py
uv run ruff format lib/video_prompt_resolver.py server/services/generation_tasks.py tests/test_video_prompt_resolver.py tests/test_generation_tasks_service.py
uv run basedpyright lib/video_prompt_resolver.py server/services/generation_tasks.py
```

期望：0 error。

- [ ] **Step 9：commit**

```bash
git add lib/video_prompt_resolver.py server/services/generation_tasks.py tests/test_video_prompt_resolver.py tests/test_generation_tasks_service.py
git commit -m "$(cat <<'EOF'
refactor(generation): 抽出 video_prompt_resolver 模块

把 _normalize_video_prompt / _collect_sheet_paths / _collect_reference_images
从 server/services/generation_tasks.py 搬到 lib/video_prompt_resolver.py，
让 worker 和后续要新增的"复制 prompt"端点共享一份装配代码，避免漂移。

行为零变化：generation_tasks 继续以 _normalize_video_prompt 别名调用，
旧测试 import 路径同步迁移。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase A · 复制 prompt + 参考图

### Task A.1 · 新建 resolver 服务层 `video_prompt_bundle.py`

把 resolver 的纯函数封装成"按 project + episode + segment 拼出 `VideoPromptBundle`"的高级 API，给 Phase A.2 的路由用。

**Files:**
- Create: `server/services/video_prompt_bundle.py`
- Create: `tests/test_video_prompt_bundle.py`

- [ ] **Step 1：写失败测试**

```python
"""resolve_video_prompt_bundle 高层 API 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.services.video_prompt_bundle import (
    VideoPromptBundle,
    VideoReferenceImage,
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
    # 应至少有 character_sheet + scene_sheet + start_image 三张
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
    # 删 character sheet 文件
    (fake_project / "characters" / "lilei.png").unlink()
    bundle = await resolve_video_prompt_bundle("fakeproject", episode=1, segment_id="001")
    kinds = [r.kind for r in bundle.reference_images]
    assert "character_sheet" not in kinds
    assert "scene_sheet" in kinds  # 其余仍应在
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_video_prompt_bundle.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `server/services/video_prompt_bundle.py`**

```python
"""video prompt bundle 服务：把项目 + episode + segment 解析成可分发到外部的 prompt 包。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lib.app_data_dir import app_data_dir
from lib.project_manager import ProjectManager
from lib.video_prompt_resolver import collect_reference_images, normalize_video_prompt

ReferenceKind = Literal[
    "character_sheet", "scene_sheet", "prop_sheet",
    "start_image", "end_image", "previous_storyboard", "extra",
]


@dataclass
class VideoReferenceImage:
    kind: ReferenceKind
    label: str            # 角色名 / 场景名 / 道具名 / "首帧 v3" 等
    relative_path: str    # 项目根相对路径
    url: str              # /api/v1/files/{project}/{relative_path}
    filename: str


@dataclass
class VideoPromptBundle:
    shot_id: str
    prompt: str
    reference_images: list[VideoReferenceImage] = field(default_factory=list)
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"


_pm = ProjectManager(app_data_dir())


def _file_url(project_name: str, relative_path: str) -> str:
    return f"/api/v1/files/{project_name}/{relative_path}"


def _load_script_for_episode(project_root: Path, project_data: dict, episode: int) -> dict | None:
    for ep in project_data.get("episodes", []):
        if ep.get("episode") == episode:
            script_file = ep.get("script_file")
            if not script_file:
                return None
            script_path = project_root / script_file
            if not script_path.exists():
                return None
            return json.loads(script_path.read_text(encoding="utf-8"))
    return None


def _find_segment(script: dict, segment_id: str) -> dict | None:
    # 兼容 narration（segments） / drama（scenes）
    for key in ("scenes", "segments"):
        for item in script.get(key, []):
            if str(item.get("scene_id") or item.get("segment_id")) == str(segment_id):
                return item
    return None


def _build_references(
    project_name: str,
    project_root: Path,
    project_data: dict,
    segment: dict,
) -> list[VideoReferenceImage]:
    refs: list[VideoReferenceImage] = []

    # character / scene / prop sheets — 复用 resolver 的策略
    target_item = {
        "characters": segment.get("characters_in_scene", []) or segment.get("characters_in_segment", []),
        "scenes": segment.get("scenes", []),
        "props": segment.get("props", []),
    }
    raw_refs = collect_reference_images(
        project_data, project_root, target_item,
        char_field="characters", scene_field="scenes", prop_field="props",
    ) or []

    # raw_refs 里 Path 是 character/scene/prop sheet 的绝对路径，按出现顺序映射 kind/label
    char_sheets = {
        (project_root / v.get("character_sheet", "")).resolve(): name
        for name, v in project_data.get("characters", {}).items() if v.get("character_sheet")
    }
    scene_sheets = {
        (project_root / v.get("scene_sheet", "")).resolve(): name
        for name, v in project_data.get("scenes", {}).items() if v.get("scene_sheet")
    }
    prop_sheets = {
        (project_root / v.get("prop_sheet", "")).resolve(): name
        for name, v in project_data.get("props", {}).items() if v.get("prop_sheet")
    }

    for ref in raw_refs:
        if not isinstance(ref, Path):
            continue  # build_previous_storyboard_reference 返回的复杂对象先跳过
        rel = ref.relative_to(project_root).as_posix()
        resolved = ref.resolve()
        if resolved in char_sheets:
            kind: ReferenceKind = "character_sheet"
            label = char_sheets[resolved]
        elif resolved in scene_sheets:
            kind = "scene_sheet"
            label = scene_sheets[resolved]
        elif resolved in prop_sheets:
            kind = "prop_sheet"
            label = prop_sheets[resolved]
        else:
            kind = "extra"
            label = ref.name
        refs.append(VideoReferenceImage(
            kind=kind, label=label,
            relative_path=rel, filename=ref.name,
            url=_file_url(project_name, rel),
        ))

    # 首帧：已生成的 storyboard image
    storyboard = project_root / "storyboards" / f"scene_{segment.get('scene_id') or segment.get('segment_id')}.png"
    if storyboard.exists():
        rel = storyboard.relative_to(project_root).as_posix()
        refs.append(VideoReferenceImage(
            kind="start_image", label="首帧",
            relative_path=rel, filename=storyboard.name,
            url=_file_url(project_name, rel),
        ))

    return refs


async def resolve_video_prompt_bundle(
    project_name: str, episode: int, segment_id: str,
) -> VideoPromptBundle:
    def _sync() -> VideoPromptBundle:
        project_root = _pm.get_project_path(project_name)
        project_data = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        script = _load_script_for_episode(project_root, project_data, episode)
        if not script:
            raise LookupError(f"episode {episode} not found")
        segment = _find_segment(script, segment_id)
        if not segment:
            raise LookupError(f"segment {segment_id} not found in episode {episode}")

        # 构造可被 normalize_video_prompt 接受的结构化 prompt
        structured = {
            "action": segment.get("action") or segment.get("text") or "",
            "camera_motion": segment.get("camera_motion") or "",
            "ambiance_audio": segment.get("ambiance_audio") or "",
            "dialogue": segment.get("dialogue") or [],
        }
        try:
            prompt = normalize_video_prompt(structured)
        except ValueError:
            # action 为空时退到字符串 fallback；都没有则抛
            text = segment.get("text") or segment.get("narration") or ""
            if not text.strip():
                raise
            prompt = normalize_video_prompt(text)

        refs = _build_references(project_name, project_root, project_data, segment)
        return VideoPromptBundle(
            shot_id=str(segment.get("scene_id") or segment.get("segment_id") or segment_id),
            prompt=prompt,
            reference_images=refs,
            duration_seconds=int(segment.get("duration_seconds") or 5),
            aspect_ratio=segment.get("aspect_ratio") or "9:16",
        )

    return await asyncio.to_thread(_sync)
```

- [ ] **Step 4：跑测试确认通过**

```bash
uv run pytest tests/test_video_prompt_bundle.py -v
```

期望：4 passed。

- [ ] **Step 5：lint + typecheck**

```bash
uv run ruff check server/services/video_prompt_bundle.py tests/test_video_prompt_bundle.py
uv run ruff format server/services/video_prompt_bundle.py tests/test_video_prompt_bundle.py
uv run basedpyright server/services/video_prompt_bundle.py
```

- [ ] **Step 6：commit**

```bash
git add server/services/video_prompt_bundle.py tests/test_video_prompt_bundle.py
git commit -m "feat(services): video_prompt_bundle resolver

按 (project, episode, segment_id) 拼出 VideoPromptBundle —— 包含
normalize 后的 final prompt 与所有参考图（角色/场景/道具 sheet + 首帧）。
为下一步 GET .../video-prompt-bundle 端点和 ZIP 打包做准备。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A.2 · 新建路由 `server/routers/shots.py` — JSON 端点

**Files:**
- Create: `server/routers/shots.py`
- Modify: `server/app.py` (注册路由)
- Create: `tests/test_routers_shots_prompt_bundle.py`

- [ ] **Step 1：写失败测试**

```python
"""GET /api/v1/projects/{p}/episodes/{e}/shots/{s}/video-prompt-bundle"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_returns_bundle_json(async_client, seeded_project):
    name = seeded_project["name"]
    resp = await async_client.get(
        f"/api/v1/projects/{name}/episodes/1/shots/001/video-prompt-bundle"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["shot_id"] == "001"
    assert "prompt" in body and len(body["prompt"]) > 0
    assert isinstance(body["reference_images"], list)


@pytest.mark.asyncio
async def test_missing_segment_404(async_client, seeded_project):
    name = seeded_project["name"]
    resp = await async_client.get(
        f"/api/v1/projects/{name}/episodes/1/shots/9999/video-prompt-bundle"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unauthorized_401(unauthorized_client, seeded_project):
    name = seeded_project["name"]
    resp = await unauthorized_client.get(
        f"/api/v1/projects/{name}/episodes/1/shots/001/video-prompt-bundle"
    )
    assert resp.status_code == 401
```

注：`async_client / unauthorized_client / seeded_project` 是 `tests/conftest.py` 既有 fixture，按现有命名找；找不到时**先 grep `tests/conftest.py` 看实际名字**再调整。

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_routers_shots_prompt_bundle.py -v
```

期望：404（路由不存在）。

- [ ] **Step 3：写路由 `server/routers/shots.py`**

```python
"""分镜级动作路由：复制 prompt、导入外部视频。"""

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


@router.get(
    "/projects/{project_name}/episodes/{episode}/shots/{segment_id}/video-prompt-bundle"
)
async def get_video_prompt_bundle(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
):
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
```

- [ ] **Step 4：注册路由 `server/app.py`**

在已有其他 router include 段落附近加：

```python
from server.routers import shots as shots_router

app.include_router(shots_router.router, prefix="/api/v1", tags=["shots"])
```

放在与 `generate` router 同段。

- [ ] **Step 5：补 i18n key**

`lib/i18n/zh/errors.py` 加：

```python
"video_prompt_bundle_not_ready": "未找到对应分镜或剧本数据",
```

`en/errors.py`：

```python
"video_prompt_bundle_not_ready": "Shot or script data not found",
```

`vi/errors.py`：

```python
"video_prompt_bundle_not_ready": "Không tìm thấy phân cảnh hoặc dữ liệu kịch bản",
```

- [ ] **Step 6：跑测试**

```bash
uv run pytest tests/test_routers_shots_prompt_bundle.py tests/test_i18n_consistency.py -v
```

期望：全绿。

- [ ] **Step 7：lint + typecheck**

```bash
uv run ruff check server/routers/shots.py tests/test_routers_shots_prompt_bundle.py
uv run ruff format server/routers/shots.py tests/test_routers_shots_prompt_bundle.py
uv run basedpyright server/routers/shots.py
```

- [ ] **Step 8：commit**

```bash
git add server/routers/shots.py server/app.py tests/test_routers_shots_prompt_bundle.py lib/i18n/
git commit -m "feat(api): GET /api/v1/.../video-prompt-bundle

返回分镜 final prompt + 参考图清单 (含 file_url)，供前端弹窗展示。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A.3 · ZIP 端点 `.../video-prompt-bundle.zip`

**Files:**
- Modify: `server/routers/shots.py` (加 endpoint)
- Modify: `tests/test_routers_shots_prompt_bundle.py` (加 zip 测试)

- [ ] **Step 1：加失败测试**

在 `tests/test_routers_shots_prompt_bundle.py` 追加：

```python
import io
import zipfile


@pytest.mark.asyncio
async def test_zip_endpoint_returns_zip(async_client, seeded_project):
    name = seeded_project["name"]
    resp = await async_client.get(
        f"/api/v1/projects/{name}/episodes/1/shots/001/video-prompt-bundle.zip"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "prompt.txt" in names
    assert "README.txt" in names
    assert any(n.startswith("references/") for n in names)

    # prompt 内容应等于 JSON 端点返回的 prompt 字段
    json_resp = await async_client.get(
        f"/api/v1/projects/{name}/episodes/1/shots/001/video-prompt-bundle"
    )
    expected_prompt = json_resp.json()["prompt"]
    assert zf.read("prompt.txt").decode("utf-8") == expected_prompt
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_routers_shots_prompt_bundle.py::test_zip_endpoint_returns_zip -v
```

期望：404。

- [ ] **Step 3：在 `server/routers/shots.py` 加 ZIP 路由**

```python
import io
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi.responses import StreamingResponse

from lib.app_data_dir import app_data_dir
from lib.project_manager import ProjectManager

_pm = ProjectManager(app_data_dir())


@router.get(
    "/projects/{project_name}/episodes/{episode}/shots/{segment_id}/video-prompt-bundle.zip"
)
async def get_video_prompt_bundle_zip(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
):
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
            f"ArcReel video prompt bundle",
            f"Project: {project_name}",
            f"Episode: {episode}",
            f"Shot: {bundle.shot_id}",
            f"Generated at: {datetime.utcnow().isoformat()}Z",
            f"Duration: {bundle.duration_seconds}s",
            f"Aspect ratio: {bundle.aspect_ratio}",
            f"",
            f"Files:",
            f"  prompt.txt        — final prompt (含反向提示词)",
            f"  references/       — 参考图，按序号排列",
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
```

- [ ] **Step 4：跑测试通过**

```bash
uv run pytest tests/test_routers_shots_prompt_bundle.py -v
```

- [ ] **Step 5：lint + commit**

```bash
uv run ruff check server/routers/shots.py
uv run ruff format server/routers/shots.py
git add server/routers/shots.py tests/test_routers_shots_prompt_bundle.py
git commit -m "feat(api): video-prompt-bundle.zip 流式 ZIP 端点

ZIP 含 prompt.txt + README.txt + references/NN_kind_filename.png，
缺失的参考图静默跳过。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A.4 · 前端 types + api.ts 方法

**Files:**
- Create: `frontend/src/types/video-prompt.ts`
- Modify: `frontend/src/api.ts` (加方法)

- [ ] **Step 1：建 types**

`frontend/src/types/video-prompt.ts`：

```typescript
export type VideoReferenceKind =
  | "character_sheet"
  | "scene_sheet"
  | "prop_sheet"
  | "start_image"
  | "end_image"
  | "previous_storyboard"
  | "extra";

export interface VideoReferenceImageDTO {
  kind: VideoReferenceKind;
  label: string;
  url: string;
  filename: string;
  relative_path: string;
}

export interface VideoPromptBundleDTO {
  shot_id: string;
  prompt: string;
  duration_seconds: number;
  aspect_ratio: string;
  reference_images: VideoReferenceImageDTO[];
}
```

- [ ] **Step 2：在 `frontend/src/api.ts` 加方法**

在 `generateVideo`（行 985 附近）之后追加：

```typescript
/**
 * 获取分镜的视频生成 prompt + 参考图（用于"复制 prompt"弹窗）
 */
static async getVideoPromptBundle(
  projectName: string,
  episode: number,
  segmentId: string,
): Promise<import("./types/video-prompt").VideoPromptBundleDTO> {
  return this.request(
    `/projects/${encodeURIComponent(projectName)}` +
    `/episodes/${episode}/shots/${encodeURIComponent(segmentId)}` +
    `/video-prompt-bundle`,
  );
}

/**
 * 返回 ZIP 下载 URL（前端用 <a download> 或 window.location 触发下载）
 */
static getVideoPromptBundleZipUrl(
  projectName: string,
  episode: number,
  segmentId: string,
): string {
  return `/api/v1/projects/${encodeURIComponent(projectName)}` +
    `/episodes/${episode}/shots/${encodeURIComponent(segmentId)}` +
    `/video-prompt-bundle.zip`;
}
```

- [ ] **Step 3：跑前端 typecheck**

```bash
cd frontend && pnpm check
```

期望：0 error。

- [ ] **Step 4：commit**

```bash
git add frontend/src/types/video-prompt.ts frontend/src/api.ts
git commit -m "feat(frontend): API.getVideoPromptBundle + ZipUrl

types/video-prompt.ts 落地 DTO；为下一步 Modal 组件做准备。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A.5 · 新建 `VideoPromptCopyModal.tsx`

**Files:**
- Create: `frontend/src/components/canvas/timeline/VideoPromptCopyModal.tsx`

- [ ] **Step 1：写组件**

```tsx
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Download, X, Check, Package } from "lucide-react";
import { API } from "@/api";
import type { VideoPromptBundleDTO } from "@/types/video-prompt";

interface Props {
  projectName: string;
  episode: number;
  segmentId: string;
  open: boolean;
  onClose: () => void;
}

export function VideoPromptCopyModal({ projectName, episode, segmentId, open, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const [bundle, setBundle] = useState<VideoPromptBundleDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [promptCopied, setPromptCopied] = useState(false);
  const [imgCopiedIdx, setImgCopiedIdx] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    API.getVideoPromptBundle(projectName, episode, segmentId)
      .then(setBundle)
      .finally(() => setLoading(false));
  }, [open, projectName, episode, segmentId]);

  if (!open) return null;

  const copyPrompt = async () => {
    if (!bundle) return;
    await navigator.clipboard.writeText(bundle.prompt);
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 1500);
  };

  const copyImage = async (url: string, idx: number) => {
    try {
      const resp = await fetch(url);
      const blob = await resp.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    } catch {
      // 回退：复制 URL
      await navigator.clipboard.writeText(window.location.origin + url);
    }
    setImgCopiedIdx(idx);
    setTimeout(() => setImgCopiedIdx(null), 1500);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("video_prompt_modal_title")}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg p-5 max-h-[90vh] overflow-y-auto"
        style={{ background: "var(--color-surface-1)", color: "var(--color-text-1)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">
            {t("video_prompt_modal_title")}
            {bundle && (
              <span className="ml-2 text-xs opacity-60">
                Shot {bundle.shot_id} · {bundle.duration_seconds}s · {bundle.aspect_ratio}
              </span>
            )}
          </h2>
          <button onClick={onClose} aria-label="close" className="focus-ring rounded p-1">
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading && <p className="text-sm opacity-60">{t("loading")}</p>}

        {bundle && (
          <>
            <div className="mb-5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold opacity-70">
                  {t("video_prompt_modal_prompt_label")}
                </span>
                <button
                  onClick={copyPrompt}
                  className="focus-ring inline-flex items-center gap-1 rounded px-2 py-1 text-xs"
                  style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
                >
                  {promptCopied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {promptCopied ? t("video_prompt_modal_copied") : t("video_prompt_modal_copy_all")}
                </button>
              </div>
              <pre
                className="max-h-60 overflow-auto rounded p-3 text-xs"
                style={{ background: "var(--color-surface-2)" }}
              >
                {bundle.prompt}
              </pre>
            </div>

            <div className="mb-5">
              <span className="text-xs font-semibold opacity-70">
                {t("video_prompt_modal_refs_label", { count: bundle.reference_images.length })}
              </span>
              {bundle.reference_images.length === 0 ? (
                <p className="mt-2 text-sm opacity-60">{t("video_prompt_modal_no_refs")}</p>
              ) : (
                <div className="mt-2 grid grid-cols-3 gap-3">
                  {bundle.reference_images.map((ref, idx) => (
                    <div
                      key={ref.relative_path}
                      className="overflow-hidden rounded"
                      style={{ border: "1px solid var(--color-hairline)" }}
                    >
                      <img
                        src={ref.url}
                        alt={ref.label}
                        className="aspect-square w-full object-cover"
                      />
                      <div className="p-2">
                        <div className="truncate text-xs font-medium">{ref.label}</div>
                        <div className="truncate text-[10px] opacity-50">{ref.filename}</div>
                        <div className="mt-1 flex gap-1">
                          <button
                            onClick={() => copyImage(ref.url, idx)}
                            className="focus-ring flex-1 inline-flex items-center justify-center gap-1 rounded p-1 text-[10px]"
                            style={{ background: "var(--color-surface-2)" }}
                          >
                            {imgCopiedIdx === idx ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                          </button>
                          <a
                            href={ref.url}
                            download={ref.filename}
                            className="focus-ring flex-1 inline-flex items-center justify-center gap-1 rounded p-1 text-[10px]"
                            style={{ background: "var(--color-surface-2)" }}
                          >
                            <Download className="h-3 w-3" />
                          </a>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <a
              href={API.getVideoPromptBundleZipUrl(projectName, episode, segmentId)}
              download
              className="focus-ring inline-flex w-full items-center justify-center gap-2 rounded py-2 text-sm"
              style={{ background: "var(--color-surface-2)", color: "var(--color-text-1)" }}
            >
              <Package className="h-4 w-4" />
              {t("video_prompt_modal_download_zip")}
            </a>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2：跑前端 typecheck（无新测试，下一步统一）**

```bash
cd frontend && pnpm check
```

期望：0 error。

- [ ] **Step 3：commit**

```bash
git add frontend/src/components/canvas/timeline/VideoPromptCopyModal.tsx
git commit -m "feat(frontend): VideoPromptCopyModal 组件

弹窗展示最终 prompt（一键复制）+ 参考图（每张图独立复制/下载）
+ ZIP 整包下载入口；图片复制失败时回退到复制 URL。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A.6 · MediaCard 加按钮 + ShotDetail 接线 + i18n

**Files:**
- Modify: `frontend/src/components/canvas/timeline/MediaCard.tsx` (header row 加 icon 按钮)
- Modify: `frontend/src/components/canvas/timeline/ShotDetail.tsx` (打开 Modal)
- Modify: `frontend/src/i18n/zh/dashboard.ts` `en/dashboard.ts` `vi/dashboard.ts`

- [ ] **Step 1：MediaCard 加 props 与按钮**

`MediaCard.tsx`，在 `MediaCardProps` 接口加：

```typescript
/** 点击复制 prompt 按钮（仅 kind=video 渲染） */
onCopyPrompt?: () => void;
```

在 header 部分（行 85-100）的 `VersionTimeMachine` 前插入：

```tsx
{kind === "video" && onCopyPrompt && (
  <button
    type="button"
    onClick={onCopyPrompt}
    title={t("media_copy_prompt_hint")}
    aria-label={t("media_copy_prompt")}
    className="focus-ring rounded p-1 transition-opacity hover:opacity-80"
    style={{ color: "var(--color-text-3)" }}
  >
    <Copy className="h-3.5 w-3.5" />
  </button>
)}
```

记得 import：

```typescript
import { Sparkles, ImageIcon, Film, Copy } from "lucide-react";
```

参数列表也加 `onCopyPrompt`：

```typescript
export function MediaCard({
  // ...,
  onCopyPrompt,
  onRestore,
}: MediaCardProps) {
```

- [ ] **Step 2：ShotDetail 接线**

`frontend/src/components/canvas/timeline/ShotDetail.tsx` 顶部 import：

```typescript
import { VideoPromptCopyModal } from "./VideoPromptCopyModal";
import { useState } from "react";
```

组件函数内 state：

```typescript
const [promptCopyShotId, setPromptCopyShotId] = useState<string | null>(null);
```

`MediaCard kind="video"`（行 617-628）加 prop：

```tsx
<MediaCard
  kind="video"
  // ...
  onCopyPrompt={() => setPromptCopyShotId(segmentId)}
  // ...
/>
```

组件返回的 JSX 末尾（`</div>` 之前）渲染 Modal：

```tsx
{promptCopyShotId && (
  <VideoPromptCopyModal
    projectName={projectName}
    episode={episode}  // ← 从 ShotDetail props 拿到的 episode 字段
    segmentId={promptCopyShotId}
    open
    onClose={() => setPromptCopyShotId(null)}
  />
)}
```

确保 `episode` / `projectName` 已经在 ShotDetail 的 props 里；不存在则从 store 取。

- [ ] **Step 3：补 i18n key**

`frontend/src/i18n/zh/dashboard.ts` 找一个合适的位置（比如 `media_*` 段尾）插入：

```typescript
media_copy_prompt: "复制 Prompt",
media_copy_prompt_hint: "复制 prompt 与参考图，去外部平台生成",
video_prompt_modal_title: "视频生成材料",
video_prompt_modal_prompt_label: "最终 Prompt",
video_prompt_modal_refs_label: "参考图（{{count}} 张）",
video_prompt_modal_copy_all: "复制全部",
video_prompt_modal_copied: "已复制",
video_prompt_modal_download_zip: "打包下载 ZIP",
video_prompt_modal_no_refs: "该镜头无参考图",
```

`en/dashboard.ts`：

```typescript
media_copy_prompt: "Copy Prompt",
media_copy_prompt_hint: "Copy prompt & references, generate elsewhere",
video_prompt_modal_title: "Video generation material",
video_prompt_modal_prompt_label: "Final Prompt",
video_prompt_modal_refs_label: "References ({{count}})",
video_prompt_modal_copy_all: "Copy all",
video_prompt_modal_copied: "Copied",
video_prompt_modal_download_zip: "Download as ZIP",
video_prompt_modal_no_refs: "No references for this shot",
```

`vi/dashboard.ts`：

```typescript
media_copy_prompt: "Sao chép Prompt",
media_copy_prompt_hint: "Sao chép prompt và ảnh tham chiếu để dùng ngoài",
video_prompt_modal_title: "Tài liệu sinh video",
video_prompt_modal_prompt_label: "Prompt cuối cùng",
video_prompt_modal_refs_label: "Ảnh tham chiếu ({{count}})",
video_prompt_modal_copy_all: "Sao chép tất cả",
video_prompt_modal_copied: "Đã sao chép",
video_prompt_modal_download_zip: "Tải xuống ZIP",
video_prompt_modal_no_refs: "Phân cảnh này không có ảnh tham chiếu",
```

- [ ] **Step 4：跑 lint + check**

```bash
cd frontend && pnpm lint && pnpm check
```

期望：0 error。

- [ ] **Step 5：跑 i18n 一致性测试**

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

期望：通过。

- [ ] **Step 6：手动验收（启动 dev server）**

参考 CLAUDE.md 的"开发命令"段：

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
# 另一终端
cd frontend && pnpm dev
```

浏览器打开 `http://localhost:5173`，进入一个已有镜头的项目，点击 "复制 Prompt" 按钮：

- [ ] Modal 正常弹出
- [ ] prompt 文本块显示完整 YAML
- [ ] 点 "复制全部"，剪贴板拿到 prompt（在外部编辑器粘贴验证）
- [ ] 参考图缩略图加载正常
- [ ] 单张图 Copy 按钮工作（粘贴到聊天/支持图的工具检查）
- [ ] 单张图 Download 按钮触发下载
- [ ] ZIP 按钮下载到完整包；解压后含 prompt.txt / README.txt / references/

如有任一项失败，回到对应 Task 修复。

- [ ] **Step 7：commit**

```bash
git add frontend/src/components/canvas/timeline/MediaCard.tsx \
        frontend/src/components/canvas/timeline/ShotDetail.tsx \
        frontend/src/i18n/zh/dashboard.ts \
        frontend/src/i18n/en/dashboard.ts \
        frontend/src/i18n/vi/dashboard.ts
git commit -m "feat(frontend): 视频卡接入复制 prompt Modal

MediaCard.kind=\"video\" header 加 Copy 图标按钮；ShotDetail 持有
selected shot state 并渲染 VideoPromptCopyModal。i18n zh/en/vi
三语补 8 个 key。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase B · 导入外部视频

### Task B.1 · 后端导入端点

**Files:**
- Modify: `server/routers/shots.py` (加 POST endpoint)
- Modify: `lib/i18n/{zh,en,vi}/errors.py` (3 个 key)
- Create: `tests/test_routers_shots_import_video.py`

- [ ] **Step 1：写失败测试**

```python
"""POST /api/v1/.../shots/{id}/import-video"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_fake_mp4(size_bytes: int = 1024) -> bytes:
    # 简易 mp4 box header + 填充字节；ffmpeg 检测时只关心格式探测，测试里 mock 掉 ffmpeg
    return b"\x00\x00\x00\x1cftypisom" + b"\x00" * (size_bytes - 12)


@pytest.mark.asyncio
async def test_import_mp4_success(async_client, seeded_project, tmp_path):
    name = seeded_project["name"]
    payload = _make_fake_mp4(2048)

    with patch("server.routers.shots._generate_thumbnail", new=lambda *a, **kw: True):
        resp = await async_client.post(
            f"/api/v1/projects/{name}/episodes/1/shots/001/import-video",
            files={"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_path"] == "videos/scene_001.mp4"
    assert body["version"] >= 1


@pytest.mark.asyncio
async def test_unsupported_extension_400(async_client, seeded_project):
    name = seeded_project["name"]
    resp = await async_client.post(
        f"/api/v1/projects/{name}/episodes/1/shots/001/import-video",
        files={"file": ("clip.avi", io.BytesIO(b"x"), "video/x-msvideo")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_oversize_413(async_client, seeded_project):
    name = seeded_project["name"]
    huge = b"x" * (201 * 1024 * 1024)  # 201 MiB
    resp = await async_client.post(
        f"/api/v1/projects/{name}/episodes/1/shots/001/import-video",
        files={"file": ("big.mp4", io.BytesIO(huge), "video/mp4")},
    )
    assert resp.status_code == 413
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_routers_shots_import_video.py -v
```

期望：404（路由不存在）。

- [ ] **Step 3：在 `server/routers/shots.py` 加路由**

```python
import asyncio
import shutil
import subprocess
from pathlib import Path
from fastapi import File, HTTPException, UploadFile
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
MAX_VIDEO_SIZE = 200 * 1024 * 1024  # 200 MiB


def _generate_thumbnail(video_path: Path, thumb_path: Path) -> bool:
    return extract_video_thumbnail(video_path, thumb_path)


async def _convert_to_mp4_if_needed(src: Path, dst: Path) -> None:
    """容器转换：mov/webm → mp4（stream copy 优先，编解码兜底）。"""
    if src.suffix.lower() == ".mp4":
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_required")

    # 优先 stream copy
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(src), "-c", "copy", str(dst),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode == 0:
        return

    # 兜底重编码
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-c:a", "aac", str(dst),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', 'ignore')[:200]}")


@router.post(
    "/projects/{project_name}/episodes/{episode}/shots/{segment_id}/import-video"
)
async def import_external_video(
    project_name: str,
    episode: int,
    segment_id: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
):
    # 1. 扩展名校验
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=_t(
            "external_video_unsupported_format",
            ext=ext, allowed=", ".join(sorted(ALLOWED_VIDEO_EXTENSIONS)),
        ))

    # 2. 流式读 + 大小校验
    payload = await file.read()
    if len(payload) > MAX_VIDEO_SIZE:
        raise HTTPException(
            status_code=413,
            detail=_t("external_video_too_large",
                      size_mb=round(len(payload) / 1024 / 1024, 1),
                      limit_mb=round(MAX_VIDEO_SIZE / 1024 / 1024, 1)),
        )

    project_root = _pm.get_project_path(project_name)
    videos_dir = project_root / "videos"
    thumbs_dir = project_root / "thumbnails"
    videos_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    target_video = videos_dir / f"scene_{segment_id}.mp4"
    target_thumb = thumbs_dir / f"scene_{segment_id}.jpg"

    # 3. 写 tmp，再做容器转换
    import tempfile
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

    # 4. 缩略图
    await asyncio.to_thread(_generate_thumbnail, target_video, target_thumb)

    # 5. VersionManager.add_version
    vm = VersionManager(project_root)
    version = await asyncio.to_thread(
        vm.add_version,
        "videos", segment_id,
        prompt="(imported external video)",
        source_file=target_video,
        source="external", original_filename=filename,
    )

    # 6. update_scene_asset
    await asyncio.to_thread(
        _pm.update_scene_asset,
        project_name, episode, segment_id,
        "video_clip", f"videos/scene_{segment_id}.mp4",
    )
    await asyncio.to_thread(
        _pm.update_scene_asset,
        project_name, episode, segment_id,
        "video_thumbnail", f"thumbnails/scene_{segment_id}.jpg",
    )

    return {
        "success": True,
        "video_path": f"videos/scene_{segment_id}.mp4",
        "thumbnail_path": f"thumbnails/scene_{segment_id}.jpg",
        "version": version,
        "url": f"/api/v1/files/{project_name}/videos/scene_{segment_id}.mp4",
    }
```

**注**：`update_scene_asset` 的真实签名以 `lib/project_manager.py:917` 为准；若签名不同，按现有调用点的样式调整入参。

- [ ] **Step 4：i18n key**

`lib/i18n/zh/errors.py`：

```python
"external_video_unsupported_format": "不支持的视频格式 {ext}（允许 {allowed}）",
"external_video_too_large": "视频过大 {size_mb}MB，上限 {limit_mb}MB",
"ffmpeg_required": "服务端缺少 ffmpeg，请联系管理员",
```

`en` / `vi` 同步。

- [ ] **Step 5：跑测试**

```bash
uv run pytest tests/test_routers_shots_import_video.py tests/test_i18n_consistency.py -v
```

期望：全绿。

- [ ] **Step 6：lint + commit**

```bash
uv run ruff check server/routers/shots.py tests/test_routers_shots_import_video.py
uv run ruff format server/routers/shots.py tests/test_routers_shots_import_video.py
uv run basedpyright server/routers/shots.py

git add server/routers/shots.py tests/test_routers_shots_import_video.py lib/i18n/
git commit -m "feat(api): POST /api/v1/.../import-video

接收 mp4/mov/webm（200 MiB 上限），mov/webm 自动 ffmpeg
容器转换；走 VersionManager 作为新版本，source=external，
emit thumbnail + 更新 project 资产指针。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task B.2 · 前端 `VideoImportModal` + api.ts

**Files:**
- Modify: `frontend/src/api.ts` (加 `importExternalVideo`)
- Create: `frontend/src/components/canvas/timeline/VideoImportModal.tsx`
- Modify: `frontend/src/i18n/{zh,en,vi}/dashboard.ts`

- [ ] **Step 1：api.ts 加方法（XHR 走进度）**

在 `api.ts` 中找一个合适位置（generateVideo 之后）追加：

```typescript
export interface ImportVideoResultDTO {
  success: boolean;
  video_path: string;
  thumbnail_path: string;
  version: number;
  url: string;
}

static importExternalVideo(
  projectName: string,
  episode: number,
  segmentId: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): Promise<ImportVideoResultDTO> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const url = `/api/v1/projects/${encodeURIComponent(projectName)}` +
      `/episodes/${episode}/shots/${encodeURIComponent(segmentId)}/import-video`;
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data?.detail ?? `HTTP ${xhr.status}`));
      } catch (e) {
        reject(e);
      }
    };
    xhr.onerror = () => reject(new Error("Network error"));
    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}
```

- [ ] **Step 2：建 `VideoImportModal.tsx`**

```tsx
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Upload, X } from "lucide-react";
import { API } from "@/api";

interface Props {
  projectName: string;
  episode: number;
  segmentId: string;
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

const ACCEPT = "video/mp4,video/quicktime,video/webm";
const MAX_SIZE = 200 * 1024 * 1024;

export function VideoImportModal({ projectName, episode, segmentId, open, onClose, onSuccess }: Props) {
  const { t } = useTranslation("dashboard");
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const onFile = (f: File) => {
    setError(null);
    if (f.size > MAX_SIZE) {
      setError(t("video_import_modal_file_too_large", { size_mb: Math.round(f.size / 1024 / 1024) }));
      return;
    }
    setFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setUploading(true);
    setProgress(0);
    try {
      await API.importExternalVideo(projectName, episode, segmentId, file, (l, total) => {
        setProgress(Math.round((l / total) * 100));
      });
      onSuccess?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("video_import_modal_title")}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg p-5"
        style={{ background: "var(--color-surface-1)", color: "var(--color-text-1)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{t("video_import_modal_title")}</h2>
          <button onClick={onClose} aria-label="close" className="focus-ring rounded p-1">
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mb-3 text-xs opacity-70">{t("video_import_modal_replace_warning")}</p>

        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          className="focus-ring w-full rounded border border-dashed py-8 text-sm"
          style={{ borderColor: "var(--color-hairline)" }}
        >
          {file ? file.name : t("video_import_modal_drag_hint")}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onFile(f);
          }}
        />

        {uploading && (
          <div className="mt-3">
            <div className="h-2 w-full overflow-hidden rounded" style={{ background: "var(--color-surface-2)" }}>
              <div className="h-full transition-all" style={{ width: `${progress}%`, background: "var(--color-accent)" }} />
            </div>
            <p className="mt-1 text-xs opacity-60">{t("video_import_modal_uploading")} {progress}%</p>
          </div>
        )}

        {error && <p className="mt-3 text-xs" style={{ color: "var(--color-error)" }}>{error}</p>}

        <button
          onClick={submit}
          disabled={!file || uploading}
          className="focus-ring mt-4 inline-flex w-full items-center justify-center gap-2 rounded py-2 text-sm font-semibold disabled:opacity-50"
          style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
        >
          <Upload className="h-4 w-4" />
          {t("video_import_modal_title")}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3：i18n key（zh）**

```typescript
media_import_video: "导入视频",
media_import_video_hint: "导入本地视频作为新版本",
video_import_modal_title: "导入外部视频",
video_import_modal_drag_hint: "点击选择 .mp4 / .mov / .webm",
video_import_modal_uploading: "上传中……",
video_import_modal_replace_warning: "导入后将作为一个新版本，原视频可在版本时间机里恢复。",
video_import_modal_file_too_large: "文件过大（{{size_mb}}MB，上限 200MB）",
```

en / vi 同步：

```typescript
// en
media_import_video: "Import Video",
media_import_video_hint: "Import a local video as a new version",
video_import_modal_title: "Import external video",
video_import_modal_drag_hint: "Click to choose .mp4 / .mov / .webm",
video_import_modal_uploading: "Uploading...",
video_import_modal_replace_warning: "Imported video will be added as a new version; the previous one is recoverable in version history.",
video_import_modal_file_too_large: "File too large ({{size_mb}}MB, limit 200MB)",

// vi
media_import_video: "Nhập video",
media_import_video_hint: "Nhập video cục bộ làm phiên bản mới",
video_import_modal_title: "Nhập video bên ngoài",
video_import_modal_drag_hint: "Bấm để chọn .mp4 / .mov / .webm",
video_import_modal_uploading: "Đang tải lên...",
video_import_modal_replace_warning: "Video nhập vào sẽ thành phiên bản mới; bản cũ vẫn khôi phục được từ lịch sử phiên bản.",
video_import_modal_file_too_large: "Tệp quá lớn ({{size_mb}}MB, giới hạn 200MB)",
```

- [ ] **Step 4：MediaCard + ShotDetail 接线**

`MediaCard.tsx` props 加：

```typescript
onImportVideo?: () => void;
```

header 处 `Copy` 按钮旁加：

```tsx
{kind === "video" && onImportVideo && (
  <button
    type="button"
    onClick={onImportVideo}
    title={t("media_import_video_hint")}
    aria-label={t("media_import_video")}
    className="focus-ring rounded p-1 transition-opacity hover:opacity-80"
    style={{ color: "var(--color-text-3)" }}
  >
    <Upload className="h-3.5 w-3.5" />
  </button>
)}
```

import：

```typescript
import { Sparkles, ImageIcon, Film, Copy, Upload } from "lucide-react";
```

`ShotDetail.tsx` 加 state + 渲染：

```typescript
const [importShotId, setImportShotId] = useState<string | null>(null);
```

```tsx
<MediaCard
  kind="video"
  // ...
  onCopyPrompt={() => setPromptCopyShotId(segmentId)}
  onImportVideo={() => setImportShotId(segmentId)}
/>

// 末尾：
{importShotId && (
  <VideoImportModal
    projectName={projectName}
    episode={episode}
    segmentId={importShotId}
    open
    onClose={() => setImportShotId(null)}
    onSuccess={() => {
      // 触发当前 episode 数据刷新，让 MediaCard 拿到新 assets
      void useProjectsStore.getState().refreshProject(projectName);
    }}
  />
)}
```

`refreshProject` 真实方法名以 `frontend/src/stores/projects-store.ts` 为准；若叫别的名（`reload` / `loadProject`），按实际方法名调整。

- [ ] **Step 5：lint + check**

```bash
cd frontend && pnpm lint && pnpm check
```

- [ ] **Step 6：手动验收**

```bash
# 后端 + 前端见 Task A.6 启动方式
```

- [ ] 任选一个有视频的镜头，点 "导入视频" 按钮
- [ ] 选一个本地 mp4 → 上传进度条走完 → Modal 关闭
- [ ] MediaCard 立即换成新视频
- [ ] 点版本时间机：列表里多一个版本（标记 external）
- [ ] 再试一个 .mov：确认 ffmpeg 转换无错
- [ ] 上传 250MB 视频：应被 413 拒绝并显示错误
- [ ] 上传 .avi：应被 400 拒绝

- [ ] **Step 7：commit**

```bash
git add frontend/src/api.ts \
        frontend/src/components/canvas/timeline/VideoImportModal.tsx \
        frontend/src/components/canvas/timeline/MediaCard.tsx \
        frontend/src/components/canvas/timeline/ShotDetail.tsx \
        frontend/src/i18n/zh/dashboard.ts \
        frontend/src/i18n/en/dashboard.ts \
        frontend/src/i18n/vi/dashboard.ts
git commit -m "feat(frontend): 导入外部视频 Modal

XHR 走上传进度；MediaCard 加 Upload 图标；ShotDetail 持有
import shot state；i18n 三语补 7 个 key。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase C · 火山小云雀视频后端

### Task C.1 · `lib/volc_visual_shared.py` — Sig v4 签名

**Files:**
- Create: `lib/volc_visual_shared.py`
- Create: `tests/test_volc_visual_signer.py`

- [ ] **Step 1：写失败测试（golden vector）**

```python
"""Volcengine Visual Service Sig v4 — 用官方文档样例做 golden test。"""

from __future__ import annotations

from datetime import datetime, timezone

from lib.volc_visual_shared import sign_request


def test_sign_request_produces_authorization_header():
    headers = sign_request(
        method="POST",
        host="visual.volcengineapi.com",
        path="/",
        query={"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"},
        headers={"Content-Type": "application/json"},
        body=b'{"req_key":"x"}',
        access_key="AKLTtest",
        secret_key="c2VjcmV0X2tleV9mb3JfdGVzdA==",
        timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("HMAC-SHA256 Credential=AKLTtest/20260528/cn-north-1/cv/request,")
    assert headers["X-Date"] == "20260528T120000Z"
    assert headers["Host"] == "visual.volcengineapi.com"
    assert "Content-Type" in headers


def test_sign_request_canonicalizes_query_lexicographically():
    h1 = sign_request(
        method="POST", host="visual.volcengineapi.com", path="/",
        query={"Version": "v", "Action": "a"},
        headers={}, body=b"",
        access_key="ak", secret_key="sk",
        timestamp=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    h2 = sign_request(
        method="POST", host="visual.volcengineapi.com", path="/",
        query={"Action": "a", "Version": "v"},  # 倒序
        headers={}, body=b"",
        access_key="ak", secret_key="sk",
        timestamp=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    assert h1["Authorization"] == h2["Authorization"]
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_volc_visual_signer.py -v
```

期望：ImportError。

- [ ] **Step 3：写 signer 实现**

`lib/volc_visual_shared.py`：

```python
"""Volcengine Visual Service Sig v4 签名工具。

参考：火山官方文档 - 公共参数 - 签名参数。
仅依赖 hashlib/hmac，无新外部依赖。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

VISUAL_HOST = "visual.volcengineapi.com"
DEFAULT_REGION = "cn-north-1"
DEFAULT_SERVICE = "cv"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_query(query: dict[str, str]) -> str:
    return "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}"
        for k, v in sorted(query.items())
    )


def sign_request(
    *,
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str = DEFAULT_REGION,
    service: str = DEFAULT_SERVICE,
    timestamp: datetime | None = None,
) -> dict[str, str]:
    """返回追加了 Authorization + X-Date + Host + X-Content-Sha256 的完整 header。"""
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    date = ts[:8]
    body_sha = _hex_sha256(body)

    out_headers: dict[str, str] = {
        **headers,
        "Host": host,
        "X-Date": ts,
        "X-Content-Sha256": body_sha,
    }

    # 1. canonical request
    signed_header_names = sorted(k.lower() for k in out_headers)
    canonical_headers = "".join(
        f"{name}:{out_headers[next(k for k in out_headers if k.lower() == name)].strip()}\n"
        for name in signed_header_names
    )
    signed_headers = ";".join(signed_header_names)
    canonical_request = (
        f"{method.upper()}\n{path}\n{_canonical_query(query)}\n"
        f"{canonical_headers}\n{signed_headers}\n{body_sha}"
    )

    # 2. string to sign
    credential_scope = f"{date}/{region}/{service}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{ts}\n{credential_scope}\n{_hex_sha256(canonical_request.encode())}"
    )

    # 3. signing key
    k_date = _hmac_sha256(secret_key.encode("utf-8"), date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out_headers["Authorization"] = (
        f"HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return out_headers
```

- [ ] **Step 4：跑测试**

```bash
uv run pytest tests/test_volc_visual_signer.py -v
```

期望：2 passed。**注意**：第一条 `Authorization` 的 startswith 断言假设 `Credential` 字段格式与火山一致；若 production 联调时发现 401，对照官方 signed sample 修正 signing 算法（最常见差异是 `payload hash` 是 hex 还是 base64、`SignedHeaders` 是否包含 host）。

- [ ] **Step 5：lint + commit**

```bash
uv run ruff check lib/volc_visual_shared.py tests/test_volc_visual_signer.py
uv run ruff format lib/volc_visual_shared.py tests/test_volc_visual_signer.py
uv run basedpyright lib/volc_visual_shared.py

git add lib/volc_visual_shared.py tests/test_volc_visual_signer.py
git commit -m "feat(lib): volc_visual_shared Sig v4 签名

visual.volcengineapi.com 的 Sig v4 签名工具，零新依赖
（仅 hashlib + hmac）；为小云雀视频后端打基础。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C.2 · `lib/volc_tos_uploader.py` — 参考图上传

**Files:**
- Create: `lib/volc_tos_uploader.py`
- Create: `tests/test_volc_tos_uploader.py`

- [ ] **Step 1：写失败测试（mock httpx）**

```python
"""TosImageUploader — 把本地图 PUT 到 TOS 取预签 URL。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.volc_tos_uploader import TosImageUploader


@pytest.fixture
def fake_image(tmp_path) -> Path:
    p = tmp_path / "ref.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return p


@pytest.mark.asyncio
async def test_upload_uploads_then_returns_presigned_url(fake_image):
    uploader = TosImageUploader(
        ak="ak", sk="sk",
        endpoint="tos-cn-beijing.volces.com",
        bucket="my-bucket", region="cn-beijing",
    )

    fake_resp = MagicMock(status_code=200)
    fake_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.put", new=AsyncMock(return_value=fake_resp)):
        url = await uploader.upload_image(fake_image)

    assert url.startswith("https://my-bucket.tos-cn-beijing.volces.com/arcreel-ref/")
    assert url.endswith(".png")
    assert "X-Tos-Date=" in url or "X-Amz-Date=" in url  # 预签 query
    assert "Signature=" in url or "X-Amz-Signature=" in url


@pytest.mark.asyncio
async def test_upload_uses_content_hash_in_key(fake_image):
    uploader = TosImageUploader(
        ak="ak", sk="sk",
        endpoint="tos-cn-beijing.volces.com",
        bucket="b", region="cn-beijing",
    )

    fake_resp = MagicMock(status_code=200)
    fake_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.put", new=AsyncMock(return_value=fake_resp)):
        u1 = await uploader.upload_image(fake_image)
        u2 = await uploader.upload_image(fake_image)

    # 同内容应有相同 key
    key1 = u1.split("?", 1)[0]
    key2 = u2.split("?", 1)[0]
    assert key1 == key2
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_volc_tos_uploader.py -v
```

期望：ImportError。

- [ ] **Step 3：写 uploader 实现**

`lib/volc_tos_uploader.py`：

```python
"""把本地图片 PUT 到火山 TOS，返回预签名 GET URL（默认 TTL 3600s）。

走 TOS 的 S3 兼容 sigv4，不引入 tos SDK 依赖。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from lib.retry import with_retry_async


def _hex_sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


class TosImageUploader:
    def __init__(self, *, ak: str, sk: str, endpoint: str, bucket: str, region: str):
        self.ak = ak
        self.sk = sk
        self.endpoint = endpoint
        self.bucket = bucket
        self.region = region
        self.service = "tos"
        self.host = f"{bucket}.{endpoint}"

    @with_retry_async()
    async def upload_image(
        self, local_path: Path, *, key_prefix: str = "arcreel-ref/", ttl_seconds: int = 3600,
    ) -> str:
        body = local_path.read_bytes()
        sha = hashlib.sha256(body).hexdigest()[:16]
        ext = local_path.suffix.lower() or ".png"
        key = f"{key_prefix}{sha}{ext}"

        # 1. PUT object
        put_url = f"https://{self.host}/{quote(key, safe='/')}"
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        date = ts[:8]
        body_hash = _hex_sha256(body)

        put_headers = {
            "Host": self.host,
            "X-Tos-Date": ts,
            "X-Tos-Content-Sha256": body_hash,
            "Content-Type": _content_type_for(ext),
        }
        put_headers["Authorization"] = self._sign_put(
            key=key, headers=put_headers, body_hash=body_hash, ts=ts, date=date,
        )

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(put_url, content=body, headers=put_headers)
            resp.raise_for_status()

        # 2. presigned GET
        expire_ts = int((now + timedelta(seconds=ttl_seconds)).timestamp())
        return self._presign_get(key=key, expire_at=expire_ts, now=now)

    def _sign_put(self, *, key: str, headers: dict[str, str], body_hash: str, ts: str, date: str) -> str:
        signed_names = sorted(k.lower() for k in headers)
        canonical_headers = "".join(
            f"{name}:{headers[next(k for k in headers if k.lower() == name)].strip()}\n"
            for name in signed_names
        )
        signed_headers = ";".join(signed_names)
        canonical_request = (
            f"PUT\n/{quote(key, safe='/')}\n\n"
            f"{canonical_headers}\n{signed_headers}\n{body_hash}"
        )
        scope = f"{date}/{self.region}/{self.service}/request"
        sts = f"TOS4-HMAC-SHA256\n{ts}\n{scope}\n{_hex_sha256(canonical_request.encode())}"
        k = _hmac(self.sk.encode(), date)
        k = _hmac(k, self.region)
        k = _hmac(k, self.service)
        k = _hmac(k, "request")
        sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        return (
            f"TOS4-HMAC-SHA256 Credential={self.ak}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={sig}"
        )

    def _presign_get(self, *, key: str, expire_at: int, now: datetime) -> str:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        date = ts[:8]
        scope = f"{date}/{self.region}/{self.service}/request"
        credential = f"{self.ak}/{scope}"
        query = {
            "X-Tos-Algorithm": "TOS4-HMAC-SHA256",
            "X-Tos-Credential": credential,
            "X-Tos-Date": ts,
            "X-Tos-Expires": str(expire_at - int(now.timestamp())),
            "X-Tos-SignedHeaders": "host",
        }
        canonical_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query.items()))
        canonical_headers = f"host:{self.host}\n"
        canonical_request = (
            f"GET\n/{quote(key, safe='/')}\n{canonical_query}\n"
            f"{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"
        )
        sts = f"TOS4-HMAC-SHA256\n{ts}\n{scope}\n{_hex_sha256(canonical_request.encode())}"
        k = _hmac(self.sk.encode(), date)
        k = _hmac(k, self.region)
        k = _hmac(k, self.service)
        k = _hmac(k, "request")
        sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        return f"https://{self.host}/{quote(key, safe='/')}?{canonical_query}&X-Tos-Signature={sig}"


def _content_type_for(ext: str) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(ext.lower(), "application/octet-stream")
```

**注意：TOS Sig v4 算法细节** 与 AWS S3 略有出入（`TOS4-HMAC-SHA256` 而非 `AWS4-...`，`X-Tos-*` 头前缀而非 `X-Amz-*`）。如果联调时 PUT 403，按官方 doc 修正前缀与算法名。

- [ ] **Step 4：跑测试**

```bash
uv run pytest tests/test_volc_tos_uploader.py -v
```

期望：2 passed（注意第二条断言关键字 `X-Tos-Date / Signature`；如算法 prefix 差异，按实际改）。

- [ ] **Step 5：lint + commit**

```bash
uv run ruff check lib/volc_tos_uploader.py tests/test_volc_tos_uploader.py
uv run ruff format lib/volc_tos_uploader.py tests/test_volc_tos_uploader.py
uv run basedpyright lib/volc_tos_uploader.py

git add lib/volc_tos_uploader.py tests/test_volc_tos_uploader.py
git commit -m "feat(lib): volc_tos_uploader 参考图上传

按 sha256 内容寻址 key，PUT + 预签名 GET（TTL 3600s）；
TOS Sig v4 手实现，无新依赖。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C.3 · `lib/video_backends/volc_xiaoyunque.py` 后端实现

**Files:**
- Modify: `lib/providers.py` (加 PROVIDER_VOLC_XIAOYUNQUE)
- Create: `lib/video_backends/volc_xiaoyunque.py`
- Modify: `lib/video_backends/__init__.py` (register)
- Create: `tests/test_volc_xiaoyunque_backend.py`

- [ ] **Step 1：加 provider 常量**

`lib/providers.py` 在已有 PROVIDER_VIDU 后追加：

```python
PROVIDER_VOLC_XIAOYUNQUE = "volc-xiaoyunque"
```

- [ ] **Step 2：写失败测试**

```python
"""VolcXiaoyunqueBackend — mock httpx + mock TOS uploader。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend


def _make_backend() -> VolcXiaoyunqueBackend:
    return VolcXiaoyunqueBackend(
        access_key="ak", secret_key="sk",
        tos_endpoint="tos-cn-beijing.volces.com",
        tos_bucket="b", tos_region="cn-beijing",
    )


@pytest.mark.asyncio
async def test_with_refs_picks_with_vinput_reqkey(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    req = VideoGenerationRequest(
        prompt="cat walks",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        duration_seconds=10,
        reference_images=[img],
    )
    submitted_payload = {}

    async def fake_submit(self, request, ref_urls):
        submitted_payload["ref_urls"] = ref_urls
        return "tid-1"

    async def fake_poll(self, task_id, request):
        return _fake_result(request)

    async def fake_upload(self, local_path, **kw):
        return f"https://x/{local_path.name}"

    with patch.object(VolcXiaoyunqueBackend, "_submit", new=fake_submit), \
         patch.object(VolcXiaoyunqueBackend, "_poll_until_done", new=fake_poll), \
         patch("lib.volc_tos_uploader.TosImageUploader.upload_image", new=fake_upload):
        backend = _make_backend()
        result = await backend.generate(req)

    assert result.video_path == req.output_path
    assert submitted_payload["ref_urls"]


@pytest.mark.asyncio
async def test_duration_mapping():
    backend = _make_backend()
    assert backend._duration_label(10) == "~15s"
    assert backend._duration_label(20) == "~30s"
    assert backend._duration_label(60) == "40~60s"
    with pytest.raises(ValueError):
        backend._duration_label(120)


def _fake_result(request):
    from lib.video_backends.base import VideoGenerationResult
    return VideoGenerationResult(
        video_path=request.output_path, provider="volc-xiaoyunque",
        model="xiaoyunque-agent-2.0", duration_seconds=request.duration_seconds,
    )
```

- [ ] **Step 3：跑测试确认失败**

```bash
uv run pytest tests/test_volc_xiaoyunque_backend.py -v
```

期望：ImportError。

- [ ] **Step 4：写 backend 实现**

`lib/video_backends/volc_xiaoyunque.py`：

```python
"""火山小云雀-智能生视频 Agent 2.0 后端。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from lib.providers import PROVIDER_VOLC_XIAOYUNQUE
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
)
from lib.volc_tos_uploader import TosImageUploader
from lib.volc_visual_shared import VISUAL_HOST, sign_request

logger = logging.getLogger(__name__)

# 文档已知
REQ_KEY_WITH_REFS = "pippit_iv2v_v20_cvtob_with_vinput"
# TODO（实施前需补全）：见 spec §13。占位字串确保走 with_refs 路径前不会误用。
REQ_KEY_WITHOUT_REFS = "TODO_NO_REF_REQ_KEY"

# TODO（实施前需 AK/SK 探活确认）：按 Volcengine "Visual" 服务一贯命名先用 GetResult
SUBMIT_ACTION = "CVSync2AsyncSubmitTask"
QUERY_ACTION = "CVSync2AsyncGetResult"
API_VERSION = "2022-08-31"


class VolcXiaoyunqueBackend:
    DEFAULT_MODEL = "xiaoyunque-agent-2.0"

    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        tos_endpoint: str,
        tos_bucket: str,
        tos_region: str,
        language: str = "Chinese",
        model: str | None = None,
        **_ignored,
    ):
        self.ak = access_key
        self.sk = secret_key
        self.language = language
        self._model = model or self.DEFAULT_MODEL
        self._uploader = TosImageUploader(
            ak=access_key, sk=secret_key,
            endpoint=tos_endpoint, bucket=tos_bucket, region=tos_region,
        )

    @property
    def name(self) -> str:
        return PROVIDER_VOLC_XIAOYUNQUE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(first_frame=True, reference_images=True, max_reference_images=50)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        ref_urls = await self._upload_refs(request)
        task_id = await self._submit(request, ref_urls)
        return await self._poll_until_done(task_id, request)

    async def _upload_refs(self, request: VideoGenerationRequest) -> list[str]:
        all_imgs: list[Path] = []
        if request.start_image:
            all_imgs.append(request.start_image)
        if request.end_image:
            all_imgs.append(request.end_image)
        if request.reference_images:
            all_imgs.extend(Path(p) if not isinstance(p, Path) else p for p in request.reference_images)

        urls: list[str] = []
        for p in all_imgs:
            if p.exists():
                urls.append(await self._uploader.upload_image(p))
        return urls

    async def _submit(self, request: VideoGenerationRequest, ref_urls: list[str]) -> str:
        req_key = REQ_KEY_WITH_REFS if ref_urls else REQ_KEY_WITHOUT_REFS
        body = {
            "req_key": req_key,
            "prompt": request.prompt,
            "ratio": request.aspect_ratio,
            "duration": self._duration_label(request.duration_seconds),
            "language": self.language,
            "enable_watermark": False,
        }
        if ref_urls:
            body["img_url_list"] = ref_urls

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        query = {"Action": SUBMIT_ACTION, "Version": API_VERSION}
        headers = sign_request(
            method="POST", host=VISUAL_HOST, path="/", query=query,
            headers={"Content-Type": "application/json"}, body=payload,
            access_key=self.ak, secret_key=self.sk,
        )
        url = f"https://{VISUAL_HOST}/?Action={SUBMIT_ACTION}&Version={API_VERSION}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, content=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 10000:
            raise RuntimeError(f"submit failed: {data}")
        task_id = data["data"]["task_id"]
        logger.info("小云雀任务已提交 task_id=%s req_key=%s", task_id, req_key)
        return task_id

    async def _poll_until_done(
        self, task_id: str, request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        async def _query():
            body = json.dumps({"req_key": REQ_KEY_WITH_REFS, "task_id": task_id}).encode("utf-8")
            query = {"Action": QUERY_ACTION, "Version": API_VERSION}
            headers = sign_request(
                method="POST", host=VISUAL_HOST, path="/", query=query,
                headers={"Content-Type": "application/json"}, body=body,
                access_key=self.ak, secret_key=self.sk,
            )
            url = f"https://{VISUAL_HOST}/?Action={QUERY_ACTION}&Version={API_VERSION}"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, content=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        result = await poll_with_retry(
            poll_fn=_query,
            is_done=lambda r: r.get("data", {}).get("status") == "done",
            is_failed=lambda r: (
                f"小云雀任务过期: {r}" if r.get("data", {}).get("status") in ("not_found", "expired") else None
            ),
            poll_interval=15,
            max_wait=1200,
            label="Xiaoyunque",
            on_progress=lambda r, elapsed: logger.info(
                "小云雀状态: %s, 已等 %ds", r.get("data", {}).get("status"), int(elapsed),
            ),
        )

        data = result["data"]
        if data.get("status") != "done":
            raise RuntimeError(f"小云雀异常状态: {data}")

        video_url = data.get("video_url")
        if not video_url:
            raise RuntimeError(f"小云雀返回缺 video_url: {data}")
        await download_video(video_url, request.output_path)

        # 解析 resp_data 拿真实时长（若可用）
        resp_data = data.get("resp_data")
        actual_duration = request.duration_seconds
        if isinstance(resp_data, str):
            try:
                rd = json.loads(resp_data)
                actual_duration = int(rd.get("Duration", actual_duration))
            except (ValueError, TypeError):
                pass

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_VOLC_XIAOYUNQUE,
            model=self._model,
            duration_seconds=actual_duration,
            video_uri=video_url,
            task_id=task_id,
        )

    @staticmethod
    def _duration_label(seconds: int) -> str:
        if seconds <= 15:
            return "~15s"
        if seconds <= 30:
            return "~30s"
        if seconds <= 60:
            return "40~60s"
        raise ValueError(f"duration {seconds}s 超出小云雀支持范围（≤ 60s）")
```

- [ ] **Step 5：注册 backend**

`lib/video_backends/__init__.py` 末尾追加：

```python
# 小云雀（火山视觉服务）
from lib.providers import PROVIDER_VOLC_XIAOYUNQUE  # noqa: E402
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend  # noqa: E402

register_backend(PROVIDER_VOLC_XIAOYUNQUE, VolcXiaoyunqueBackend)
```

`__all__` 加 `"PROVIDER_VOLC_XIAOYUNQUE"`。

- [ ] **Step 6：跑测试**

```bash
uv run pytest tests/test_volc_xiaoyunque_backend.py -v
```

期望：2 passed。

- [ ] **Step 7：lint + commit**

```bash
uv run ruff check lib/video_backends/volc_xiaoyunque.py lib/video_backends/__init__.py lib/providers.py tests/test_volc_xiaoyunque_backend.py
uv run ruff format lib/video_backends/volc_xiaoyunque.py lib/video_backends/__init__.py lib/providers.py tests/test_volc_xiaoyunque_backend.py
uv run basedpyright lib/video_backends/volc_xiaoyunque.py

git add lib/providers.py lib/video_backends/volc_xiaoyunque.py lib/video_backends/__init__.py tests/test_volc_xiaoyunque_backend.py
git commit -m "feat(video): 火山小云雀视频后端

调用 visual.volcengineapi.com (Sig v4)，参考图先 PUT 到 TOS 取签名 URL
再传给 API；按是否有参考图自动分发到「有参考」req_key（无参考的 req_key
和查询 Action 名留 TODO，待联调确认）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C.4 · provider registry + worker pool 注册

**Files:**
- Modify: `lib/config/registry.py` (加 ProviderMeta)
- Modify: `lib/generation_worker.py` (加池配置)
- Modify: `lib/i18n/{zh,en,vi}/providers.py`
- Create: `tests/test_provider_registry_volc_xiaoyunque.py`

- [ ] **Step 1：测试**

```python
"""provider registry 入口存在"""
from lib.config.registry import PROVIDER_REGISTRY
from lib.providers import PROVIDER_VOLC_XIAOYUNQUE


def test_xiaoyunque_provider_registered():
    assert PROVIDER_VOLC_XIAOYUNQUE in PROVIDER_REGISTRY
    meta = PROVIDER_REGISTRY[PROVIDER_VOLC_XIAOYUNQUE]
    assert "access_key" in meta.required_keys
    assert "secret_key" in meta.required_keys
    assert "tos_bucket" in meta.required_keys
    assert "xiaoyunque-agent-2.0" in meta.models
    assert meta.models["xiaoyunque-agent-2.0"].media_type == "video"


def test_xiaoyunque_backend_registered():
    from lib.video_backends import get_registered_backends
    assert PROVIDER_VOLC_XIAOYUNQUE in get_registered_backends()
```

- [ ] **Step 2：跑测试确认失败**

```bash
uv run pytest tests/test_provider_registry_volc_xiaoyunque.py -v
```

期望：第一条 AssertionError。

- [ ] **Step 3：`lib/config/registry.py` 加 ProviderMeta**

在 PROVIDER_REGISTRY 字典末尾追加（保留尾逗号）：

```python
"volc-xiaoyunque": ProviderMeta(
    display_name="火山小云雀",
    description="火山引擎智能生视频 Agent 2.0，支持有参考图与纯文本两种模式；"
                "参考图需通过同账号 TOS bucket 走签名 URL 提供。",
    required_keys=["access_key", "secret_key", "tos_endpoint", "tos_bucket", "tos_region"],
    optional_keys=["video_max_workers", "language"],
    secret_keys=["access_key", "secret_key"],
    models={
        "xiaoyunque-agent-2.0": ModelInfo(
            display_name="智能生视频 Agent 2.0",
            media_type="video",
            capabilities=["text_to_video", "image_to_video"],
            default=True,
            supported_durations=[15, 30, 60],
            resolutions=[],
        ),
    },
    default_base_url="https://visual.volcengineapi.com",
),
```

- [ ] **Step 4：worker pool 配置**

`lib/generation_worker.py` 找到 `Worker 初始池配置` 那个 dict（grep `'gemini-aistudio'` 找到所在位置），加：

```python
'volc-xiaoyunque': (5, 3),
```

- [ ] **Step 5：i18n providers**

`lib/i18n/zh/providers.py` 加：

```python
"volc-xiaoyunque.display_name": "火山小云雀",
"volc-xiaoyunque.description": "火山引擎智能生视频 Agent 2.0",
```

en：

```python
"volc-xiaoyunque.display_name": "Volcengine Xiaoyunque",
"volc-xiaoyunque.description": "Volcengine Visual Smart Video Agent 2.0",
```

vi：

```python
"volc-xiaoyunque.display_name": "Volcengine Xiaoyunque",
"volc-xiaoyunque.description": "Volcengine Visual Smart Video Agent 2.0",
```

- [ ] **Step 6：跑测试**

```bash
uv run pytest tests/test_provider_registry_volc_xiaoyunque.py tests/test_i18n_consistency.py -v
```

期望：通过。

- [ ] **Step 7：lint + commit**

```bash
uv run ruff check lib/config/registry.py lib/generation_worker.py tests/test_provider_registry_volc_xiaoyunque.py
uv run ruff format lib/config/registry.py lib/generation_worker.py tests/test_provider_registry_volc_xiaoyunque.py

git add lib/config/registry.py lib/generation_worker.py lib/i18n/ tests/test_provider_registry_volc_xiaoyunque.py
git commit -m "feat(config): 注册小云雀 provider + worker 池

PROVIDER_REGISTRY 加 volc-xiaoyunque ProviderMeta，worker 池
配置 (5, 3)；i18n providers 三语补 display_name/description。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C.5 · 集成 smoke：完整跑通假任务

**Files:**
- Create: `tests/test_volc_xiaoyunque_e2e_mock.py`

- [ ] **Step 1：mock httpx 走通完整 submit → poll → download**

```python
"""mock 完整调用链：先 submit 拿 task_id，再轮询直到 done，下载 mp4。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend


@pytest.mark.asyncio
async def test_end_to_end_flow_with_refs(tmp_path):
    ref_img = tmp_path / "ref.png"
    ref_img.write_bytes(b"\x89PNG")

    req = VideoGenerationRequest(
        prompt="cat in space",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="16:9",
        duration_seconds=15,
        reference_images=[ref_img],
    )

    backend = VolcXiaoyunqueBackend(
        access_key="ak", secret_key="sk",
        tos_endpoint="tos-cn-beijing.volces.com",
        tos_bucket="b", tos_region="cn-beijing",
    )

    # mock TOS upload
    async def fake_upload(self, local_path, **kw):
        return f"https://x/{local_path.name}"

    # mock httpx.post：第一次 submit 返回 task_id；其后查询返回 done
    call_count = {"n": 0}

    class FakeResp:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200
        def raise_for_status(self): pass
        def json(self): return self._p

    async def fake_post(self, url, *, content=None, headers=None):
        if "CVSync2AsyncSubmitTask" in url:
            return FakeResp({"code": 10000, "data": {"task_id": "tid-99"}})
        if "CVSync2AsyncGetResult" in url:
            call_count["n"] += 1
            if call_count["n"] < 2:
                return FakeResp({"code": 10000, "data": {"status": "generating"}})
            return FakeResp({"code": 10000, "data": {
                "status": "done", "video_url": "https://x/out.mp4",
                "resp_data": '{"Duration": 15}',
            }})
        raise AssertionError(url)

    async def fake_download(url, output_path, **kw):
        Path(output_path).write_bytes(b"\x00\x00\x00\x1cftypisom")

    with patch("lib.volc_tos_uploader.TosImageUploader.upload_image", new=fake_upload), \
         patch("httpx.AsyncClient.post", new=fake_post), \
         patch("lib.video_backends.volc_xiaoyunque.download_video", new=fake_download):
        result = await backend.generate(req)

    assert result.video_path == req.output_path
    assert result.video_path.exists()
    assert result.task_id == "tid-99"
    assert result.duration_seconds == 15
```

- [ ] **Step 2：跑测试**

```bash
uv run pytest tests/test_volc_xiaoyunque_e2e_mock.py -v
```

注：`poll_with_retry` 的 `poll_interval=15` 在测试里会让 sleep 真实 15s。改写测试时如果跑得慢，可用 monkeypatch 把 `asyncio.sleep` 替成 no-op：

```python
@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
```

- [ ] **Step 3：commit**

```bash
git add tests/test_volc_xiaoyunque_e2e_mock.py
git commit -m "test(volc): 小云雀 backend e2e mock 串通

完整链路：upload → submit → poll generating × N → done → download，
mp4 落盘 + task_id + duration 都对。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C.6 · BLOCKED · 联调（需用户提供）

**前置阻塞**：

1. 用户提供"小云雀-智能生视频 Agent 2.0 **无参考-接口文档**"完整内容
2. 用户提供一对可用的 火山 AK/SK + TOS bucket
3. （可选）单价信息用于 cost_calculator

**待补改动**：

- [ ] 把 `lib/video_backends/volc_xiaoyunque.py::REQ_KEY_WITHOUT_REFS` 填准（去掉 `TODO_` 前缀）
- [ ] 用 AK/SK 真实跑一次「有参考」+「无参考」任务，确认 `QUERY_ACTION = "CVSync2AsyncGetResult"` 是否就是这个名字；不对则 patch
- [ ] 跑通后把 dev 启动起来，在 settings 页填配置 → 选小云雀做 video provider → 生成 1 个镜头视频 → 截屏存证（手动验收）
- [ ] 单价进 `lib/cost_calculator.py`（具体位置：grep `provider_cost` 找到 cost 表，添加 `volc-xiaoyunque` 条目）

提交：

```bash
git commit -m "feat(volc): 联调小云雀有参考 + 无参考接口

填准 REQ_KEY_WITHOUT_REFS，确认 GetResult Action 名；本地跑通
单镜头生成并截屏。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## 最终验收（三件事都做完后）

- [ ] `uv run pytest --cov` 覆盖率 ≥ 80%
- [ ] `uv run basedpyright` 0 error
- [ ] `uv run ruff check . && uv run ruff format --check .` 0 issue
- [ ] `cd frontend && pnpm lint && pnpm check && pnpm build` 全绿
- [ ] 手动跑：
  - [ ] 复制 prompt 弹窗工作正常（含 ZIP 下载）
  - [ ] 导入 mp4 视频成为新版本
  - [ ] 小云雀作为 provider 能跑出视频（联调后）
- [ ] PR 描述里列出三件事 + 5 个 spec TODO 中已完成的部分

---

## Self-Review

**1. Spec coverage**：
- §5 复制 prompt + 参考图 → Task A.1 ~ A.6 ✓
- §6 导入外部视频 → Task B.1 ~ B.2 ✓
- §7 小云雀 → Task C.1 ~ C.6 ✓
- §8 数据模型 → Phase B Step 5 走 VersionManager.add_version(..., source="external") ✓
- §9 错误处理 → 各 Task 都有 i18n key + 400/413/500 分类 ✓
- §10 测试 → 每 Task 都先写测试 ✓
- §11 i18n → Task A.6 / B.2 / C.4 三处补全 zh/en/vi ✓
- §12 实施顺序 → Phase 0 → A → B → C，跟 spec 完全一致 ✓
- §13 TODO → C.6 显式标 BLOCKED ✓

**2. Placeholder scan**：所有"TODO"都在 spec §13 已明示的 4 处，且作为 BLOCKED 的明确指示存在 Task C.6 — 不是 plan 失败。

**3. Type consistency**：
- `VideoPromptBundle` / `VideoReferenceImage` 在 Task A.1 定义，A.2/A.3 后端 + A.4 前端 DTO + A.5 Modal 都按相同字段名（`shot_id` / `prompt` / `reference_images` / `kind` / `label` / `url` / `filename` / `relative_path`）✓
- `VolcXiaoyunqueBackend.__init__` 关键字参数（`access_key` / `secret_key` / `tos_endpoint` / `tos_bucket` / `tos_region` / `language`）在 C.3 / C.4 / C.5 三处一致 ✓
- `importExternalVideo` 前端方法与后端 `import_external_video` 返回结构（`video_path` / `thumbnail_path` / `version` / `url`）一致 ✓

修复无遗漏，结束。
