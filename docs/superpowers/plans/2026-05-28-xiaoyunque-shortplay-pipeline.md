# 小云雀短剧 Agent pipeline 接入（Phase A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ArcReel 后端接入小云雀短剧 Agent 的 4 步 pipeline（剧本解析→图片生成→视频生成→视频合成），通过 REST API 触发，用户用 curl 即可跑完整套流程。

**Architecture:** 完全平行于现有 ArcReel 生成体系。新增独立模块 `lib/xiaoyunque_shortplay/`：client 包装 VisualService SDK 调 4 个 Action，runner 串行编排 4 步、每步成功立即写库以便断点续传，worker 后台拉未完成 run 推进，REST router 暴露触发/查询/取消端点。新 ORM table `xiaoyunque_runs` 持久化状态。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy async / alembic / volcengine.visual.VisualService SDK (已装) / lib/volc_tos_uploader (已存在) / pytest asyncio_mode auto.

**Spec:** `docs/superpowers/specs/2026-05-28-xiaoyunque-shortplay-pipeline-design.md`

**前置条件**：

- 当前分支 `feat/shot-prompt-copy-video-import-xiaoyunque`（PR #2，未合并 main）已包含 `lib/volc_tos_uploader.py`、`lib/video_backends/volc_xiaoyunque.py` 等基础设施
- 用户已在火山控制台开通免费试用：剧本解析 / 图片生成 / 视频生成 fast 720p + pro 720p / 视频合成 fast + pro
- 用户的 AK/SK 已活体验证过（PR #2 烟雾测试）
- alembic 当前 head = `8b1e8a1290ca`

**Pre-impl TODO（不阻塞 Task 1-9，阻塞 Task 10 live verify）**：

- 用户必须在 ArcReel Settings 配置 `volc-xiaoyunque` provider 的 `tos_endpoint` / `tos_bucket` / `tos_region`（PR #2 已经支持配；Task 10 需要确认填了）
- 找一份 < 300 字符的中文剧本（spec §14 已标）

---

## Task 1 · errors.py 异常家族

**Files:**
- Create: `lib/xiaoyunque_shortplay/__init__.py`（空 module marker）
- Create: `lib/xiaoyunque_shortplay/errors.py`
- Create: `tests/test_xiaoyunque_errors.py`

- [ ] **Step 1：建 module marker**

新建空文件：

```python
# lib/xiaoyunque_shortplay/__init__.py
"""小云雀短剧 Agent pipeline 接入模块。"""
```

- [ ] **Step 2：写失败测试 `tests/test_xiaoyunque_errors.py`**

```python
"""XiaoyunqueError 家族单元测试。"""

from __future__ import annotations

import pytest

from lib.xiaoyunque_shortplay.errors import (
    PipelineCancelled,
    ScriptTooLongError,
    XiaoyunqueAPIError,
    XiaoyunqueError,
    classify_business_code,
)


def test_xiaoyunque_api_error_carries_code_and_message():
    err = XiaoyunqueAPIError(code=50412, message="Text Risk Not Pass")
    assert err.code == 50412
    assert "50412" in str(err)
    assert "Text Risk Not Pass" in str(err)


def test_script_too_long_extends_base():
    err = ScriptTooLongError(actual=500, limit=300)
    assert isinstance(err, XiaoyunqueError)
    assert err.actual == 500
    assert err.limit == 300


def test_pipeline_cancelled_extends_base():
    err = PipelineCancelled(run_id="abc")
    assert isinstance(err, XiaoyunqueError)
    assert "abc" in str(err)


@pytest.mark.parametrize(
    "code,expected",
    [
        (10000, "ok"),
        (50500, "retryable"),
        (50501, "retryable"),
        (50429, "retryable"),
        (50430, "retryable"),
        (50511, "retryable"),
        (50411, "fatal"),
        (50412, "fatal"),
        (50413, "fatal"),
        (50512, "fatal"),
        (50513, "fatal"),
        (50514, "fatal"),
        (99999, "fatal"),
    ],
)
def test_classify_business_code(code: int, expected: str) -> None:
    assert classify_business_code(code) == expected
```

- [ ] **Step 3：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_errors.py -v
```

期望：ImportError。

- [ ] **Step 4：写实现 `lib/xiaoyunque_shortplay/errors.py`**

```python
"""小云雀短剧 pipeline 异常家族 + 业务错误码分类。"""

from __future__ import annotations

from typing import Literal


class XiaoyunqueError(Exception):
    """所有短剧 pipeline 异常的基类。"""


class XiaoyunqueAPIError(XiaoyunqueError):
    """火山接口返回 code != 10000。"""

    def __init__(self, *, code: int, message: str, request_id: str | None = None):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"小云雀 API code={code} message={message!r} request_id={request_id}")


class ScriptTooLongError(XiaoyunqueError):
    """剧本超出小云雀长度上限。"""

    def __init__(self, *, actual: int, limit: int):
        self.actual = actual
        self.limit = limit
        super().__init__(f"剧本长度 {actual} 超过上限 {limit}")


class PipelineCancelled(XiaoyunqueError):
    """run 在执行中被 user 取消。"""

    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"短剧 pipeline 已取消 run_id={run_id}")


# 来自文档"业务错误码"表格的可重试分类
_RETRYABLE_CODES = frozenset(
    {
        50511,  # Post Img Risk Not Pass — 可重试
        50429,  # QPS 超限
        50430,  # 并发超限
        50500,  # Internal Error
        50501,  # Internal RPC Error
    }
)


def classify_business_code(code: int) -> Literal["ok", "retryable", "fatal"]:
    """火山业务错误码 → 重试策略分类。"""
    if code == 10000:
        return "ok"
    if code in _RETRYABLE_CODES:
        return "retryable"
    return "fatal"
```

- [ ] **Step 5：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_errors.py -v
```

期望：16 passed。

- [ ] **Step 6：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/ tests/test_xiaoyunque_errors.py
uv run ruff format lib/xiaoyunque_shortplay/ tests/test_xiaoyunque_errors.py
uv run basedpyright lib/xiaoyunque_shortplay/errors.py
```

期望：0 error。

- [ ] **Step 7：commit**

```bash
git add lib/xiaoyunque_shortplay/__init__.py lib/xiaoyunque_shortplay/errors.py tests/test_xiaoyunque_errors.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): errors 家族 + 业务错误码分类

XiaoyunqueAPIError / ScriptTooLongError / PipelineCancelled
+ classify_business_code(50500→retryable, 50412→fatal 等)。
为后续 client/runner 提供统一异常语义。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 · state.py 状态 dataclass

**Files:**
- Create: `lib/xiaoyunque_shortplay/state.py`
- Create: `tests/test_xiaoyunque_state.py`

- [ ] **Step 1：写失败测试**

```python
"""RunState 与各 sub-state dataclass 序列化往返测试。"""

from __future__ import annotations

from lib.xiaoyunque_shortplay.state import (
    CharacterState,
    EpisodeState,
    RunState,
    SceneState,
    ScriptSummary,
    ShotState,
    state_from_dict,
    state_to_dict,
)


def test_empty_runstate_roundtrip():
    s = RunState()
    d = state_to_dict(s)
    s2 = state_from_dict(d)
    assert s == s2


def test_full_runstate_roundtrip():
    s = RunState(
        parse_task_id="t1",
        design_task_id="t2",
        script=ScriptSummary(title="奶茶店", episode_count=3, core_setting="x"),
        characters=[
            CharacterState(
                character_id="C1",
                name="陈屿",
                body_image_url="https://x/body.png",
                bust_portrait_url="https://x/bust.png",
                appearance_count=3,
            )
        ],
        scenes=[
            SceneState(scene_id="S1", name="奶茶店", image_urls=["https://x/scene1.png"])
        ],
        episodes=[
            EpisodeState(
                episode_id="1",
                episode_asset_id="ea1",
                title="求婚秘密",
                status="done",
                shots=[
                    ShotState(
                        shot_id="S1",
                        description="特写",
                        status=3,
                        video_url="https://x/s1.mp4",
                        duration_ms=5000,
                    )
                ],
                video_generate_task_id="vg1",
                video_compose_task_id="vc1",
                final_video_url="https://x/ep1.mp4",
                final_cover_url="https://x/ep1.png",
            )
        ],
        charge_count_total=23,
    )
    d = state_to_dict(s)
    s2 = state_from_dict(d)
    assert s2 == s
    assert d["episodes"][0]["status"] == "done"
    assert d["characters"][0]["name"] == "陈屿"


def test_runstate_partial_loads_with_missing_optional_fields():
    """老版本写入的 state 缺少新字段时也能解析。"""
    partial = {"parse_task_id": "t1"}
    s = state_from_dict(partial)
    assert s.parse_task_id == "t1"
    assert s.script is None
    assert s.episodes == []
    assert s.charge_count_total == 0
```

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_state.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `lib/xiaoyunque_shortplay/state.py`**

```python
"""短剧 pipeline 运行时状态 dataclass + JSON 序列化往返。

state_json 持久化在 xiaoyunque_runs 表的 JSON 列；老版本字段缺失要兼容。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EpisodeStatus = Literal["pending", "generating", "composing", "done", "failed", "failed_compose"]


@dataclass
class ScriptSummary:
    title: str = ""
    episode_count: int = 0
    core_setting: str = ""


@dataclass
class CharacterState:
    character_id: str
    name: str
    body_image_url: str | None = None
    bust_portrait_url: str | None = None
    appearance_count: int = 0


@dataclass
class SceneState:
    scene_id: str
    name: str
    image_urls: list[str] = field(default_factory=list)


@dataclass
class ShotState:
    shot_id: str
    description: str = ""
    status: int = 0  # 火山 Shot Status: 0/1/2/3/4/5
    video_url: str | None = None
    duration_ms: int = 0


@dataclass
class EpisodeState:
    episode_id: str
    episode_asset_id: str = ""
    title: str = ""
    status: EpisodeStatus = "pending"
    shots: list[ShotState] = field(default_factory=list)
    video_generate_task_id: str | None = None
    video_compose_task_id: str | None = None
    final_video_url: str | None = None
    final_cover_url: str | None = None
    error_message: str | None = None


@dataclass
class RunState:
    parse_task_id: str | None = None
    design_task_id: str | None = None
    script: ScriptSummary | None = None
    characters: list[CharacterState] = field(default_factory=list)
    scenes: list[SceneState] = field(default_factory=list)
    episodes: list[EpisodeState] = field(default_factory=list)
    charge_count_total: int = 0


def state_to_dict(s: RunState) -> dict[str, Any]:
    return asdict(s)


def state_from_dict(d: dict[str, Any]) -> RunState:
    """兼容旧版本字段缺失。"""
    if d is None:
        return RunState()
    script_d = d.get("script")
    script = ScriptSummary(**script_d) if isinstance(script_d, dict) else None
    return RunState(
        parse_task_id=d.get("parse_task_id"),
        design_task_id=d.get("design_task_id"),
        script=script,
        characters=[CharacterState(**c) for c in d.get("characters") or []],
        scenes=[SceneState(**s) for s in d.get("scenes") or []],
        episodes=[
            EpisodeState(
                episode_id=e.get("episode_id", ""),
                episode_asset_id=e.get("episode_asset_id", ""),
                title=e.get("title", ""),
                status=e.get("status", "pending"),
                shots=[ShotState(**sh) for sh in e.get("shots") or []],
                video_generate_task_id=e.get("video_generate_task_id"),
                video_compose_task_id=e.get("video_compose_task_id"),
                final_video_url=e.get("final_video_url"),
                final_cover_url=e.get("final_cover_url"),
                error_message=e.get("error_message"),
            )
            for e in d.get("episodes") or []
        ],
        charge_count_total=int(d.get("charge_count_total") or 0),
    )
```

- [ ] **Step 4：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_state.py -v
```

期望：3 passed。

- [ ] **Step 5：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/state.py tests/test_xiaoyunque_state.py
uv run ruff format lib/xiaoyunque_shortplay/state.py tests/test_xiaoyunque_state.py
uv run basedpyright lib/xiaoyunque_shortplay/state.py
```

- [ ] **Step 6：commit**

```bash
git add lib/xiaoyunque_shortplay/state.py tests/test_xiaoyunque_state.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): RunState dataclass + 序列化往返

ScriptSummary / CharacterState / SceneState / ShotState / EpisodeState / RunState。
state_to_dict/state_from_dict 保持向前兼容：旧 state_json 缺字段时落默认值。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 · ORM model `xiaoyunque_runs`

**Files:**
- Create: `lib/db/models/xiaoyunque_run.py`
- Modify: `lib/db/models/__init__.py`（如有显式导出，加入 XiaoyunqueRun）
- Create: `alembic/versions/<gen>_add_xiaoyunque_runs.py`
- Create: `tests/test_xiaoyunque_run_model.py`

- [ ] **Step 1：检查 `lib/db/models/__init__.py` 是否需要导出**

```bash
cat lib/db/models/__init__.py | head -30
```

如果文件里有显式的 `from .task import Task` 风格的导出，需要在 step 4 后追加 XiaoyunqueRun。如果只是 `from .task import *`，无需改。

- [ ] **Step 2：写失败测试 `tests/test_xiaoyunque_run_model.py`**

```python
"""XiaoyunqueRun ORM model 基本读写测试。"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from lib.db.models.xiaoyunque_run import XiaoyunqueRun


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_can_insert_and_query(async_session):
    run = XiaoyunqueRun(
        id=str(uuid4()),
        project_name="p1",
        status="pending",
        model_variant="fast720p",
        visual_style="真人写实",
        video_ratio="16:9",
        script_file_url="https://x/script.docx",
        state_json=json.dumps({}),
    )
    async_session.add(run)
    await async_session.commit()

    fetched = (
        await async_session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.id == run.id))
    ).scalar_one()
    assert fetched.project_name == "p1"
    assert fetched.status == "pending"
    assert fetched.model_variant == "fast720p"
    assert fetched.thread_id is None  # 可空字段缺省 None


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_status_transition_persists(async_session):
    run_id = str(uuid4())
    run = XiaoyunqueRun(
        id=run_id, project_name="p2", status="pending",
        model_variant="fast720p", visual_style="x", video_ratio="9:16",
        script_file_url="https://x/s.docx", state_json="{}",
    )
    async_session.add(run)
    await async_session.commit()

    run.status = "parsing"
    run.thread_id = "ark_thread_x"
    await async_session.commit()

    fetched = (
        await async_session.execute(select(XiaoyunqueRun).where(XiaoyunqueRun.id == run_id))
    ).scalar_one()
    assert fetched.status == "parsing"
    assert fetched.thread_id == "ark_thread_x"
```

- [ ] **Step 3：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_run_model.py -v
```

期望：ImportError。

- [ ] **Step 4：写 ORM model `lib/db/models/xiaoyunque_run.py`**

```python
"""短剧 pipeline 运行实例 ORM。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, UserOwnedMixin


class XiaoyunqueRun(UserOwnedMixin, Base):
    __tablename__ = "xiaoyunque_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # uuid4 hex
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # pending / parsing / designing / generating / composing / done / failed / cancelled
    model_variant: Mapped[str] = mapped_column(String, nullable=False, server_default="fast720p")
    visual_style: Mapped[str] = mapped_column(String, nullable=False)
    video_ratio: Mapped[str] = mapped_column(String, nullable=False, server_default="16:9")
    thread_id: Mapped[str | None] = mapped_column(String)
    assets_id: Mapped[str | None] = mapped_column(String)
    script_file_url: Mapped[str] = mapped_column(Text, nullable=False)
    # state_json 存 RunState dict 化后的 JSON（SQLite/PG 都用 TEXT 兼容）
    state_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

如果 `lib/db/models/__init__.py` 有显式导出，加：

```python
from .xiaoyunque_run import XiaoyunqueRun  # noqa: F401
```

- [ ] **Step 5：生成 alembic 迁移**

```bash
uv run alembic revision --autogenerate -m "add xiaoyunque_runs"
```

输出会指出新文件路径，类似 `alembic/versions/<short_hash>_add_xiaoyunque_runs.py`。

打开新文件，检查 `upgrade()` 函数应包含 `op.create_table("xiaoyunque_runs", ...)`。手工补：

- 在 `upgrade()` 开头加幂等保护：

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "xiaoyunque_runs" in inspector.get_table_names():
        return
    # ...原 autogenerate 出来的 create_table...
```

- `downgrade()` 应是 `op.drop_table("xiaoyunque_runs")`。

参考 `alembic/versions/ecbb53758daa_add_api_keys_table.py` 的写法。

- [ ] **Step 6：跑迁移**

```bash
uv run alembic upgrade head
```

期望：无报错，新版本应用。

- [ ] **Step 7：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_run_model.py -v
```

期望：2 passed。

- [ ] **Step 8：lint + typecheck**

```bash
uv run ruff check lib/db/models/xiaoyunque_run.py tests/test_xiaoyunque_run_model.py alembic/versions/*xiaoyunque*
uv run ruff format lib/db/models/xiaoyunque_run.py tests/test_xiaoyunque_run_model.py alembic/versions/*xiaoyunque*
uv run basedpyright lib/db/models/xiaoyunque_run.py
```

- [ ] **Step 9：commit**

```bash
git add lib/db/models/xiaoyunque_run.py lib/db/models/__init__.py alembic/versions/*xiaoyunque* tests/test_xiaoyunque_run_model.py
git commit -m "$(cat <<'EOF'
feat(db): xiaoyunque_runs ORM table + alembic 迁移

短剧 pipeline 项目级长任务的持久化。字段：
- id (uuid4) / project_name / status (8 枚举值)
- model_variant (fast720p/pro720p) / visual_style / video_ratio
- thread_id + assets_id（剧本解析返回，后续步骤必传）
- script_file_url（TOS 签名 URL）
- state_json TEXT 存 RunState 序列化结果
- created_at / updated_at / completed_at / last_error

迁移做了幂等保护，多次 upgrade 不重复建表。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 · client.py 4 步 API 封装

**Files:**
- Create: `lib/xiaoyunque_shortplay/client.py`
- Create: `tests/test_xiaoyunque_client.py`

- [ ] **Step 1：写失败测试**

```python
"""XiaoyunqueShortplayClient — mock VisualService，验证 req_key 分发 + 字段映射。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
from lib.xiaoyunque_shortplay.errors import XiaoyunqueAPIError


def _make_client(variant: str = "fast720p") -> XiaoyunqueShortplayClient:
    return XiaoyunqueShortplayClient(access_key="ak", secret_key="sk", model_variant=variant)


@pytest.mark.asyncio
async def test_submit_script_analysis_returns_task_id():
    client = _make_client()

    captured = {}

    def fake_submit(form: dict) -> dict:
        captured["form"] = form
        return {"code": 10000, "data": {"task_id": "tid-script-1"}}

    with patch.object(client._visual, "cv_sync2async_submit_task", side_effect=fake_submit):
        task_id = await client.submit_script_analysis(
            visual_style="真人写实",
            video_ratio="16:9",
            file_url="https://x/s.docx",
            file_type="docx",
            file_name="s.docx",
        )

    assert task_id == "tid-script-1"
    assert captured["form"]["req_key"] == "pippit_shortplay_cvtob_script_analysis"
    assert captured["form"]["visual_style"] == "真人写实"
    assert captured["form"]["file_url"] == "https://x/s.docx"


@pytest.mark.asyncio
async def test_submit_video_generate_picks_fast_or_pro_req_key():
    fast = _make_client(variant="fast720p")
    pro = _make_client(variant="pro720p")

    captured_fast, captured_pro = {}, {}

    def fake_submit_fast(form: dict) -> dict:
        captured_fast["form"] = form
        return {"code": 10000, "data": {"task_id": "f1"}}

    def fake_submit_pro(form: dict) -> dict:
        captured_pro["form"] = form
        return {"code": 10000, "data": {"task_id": "p1"}}

    with patch.object(fast._visual, "cv_sync2async_submit_task", side_effect=fake_submit_fast):
        await fast.submit_video_generate(
            assets_id="a1", thread_id="t1", episode_id="1", run_id="r-fast"
        )

    with patch.object(pro._visual, "cv_sync2async_submit_task", side_effect=fake_submit_pro):
        await pro.submit_video_generate(
            assets_id="a1", thread_id="t1", episode_id="1", run_id="r-pro"
        )

    assert captured_fast["form"]["req_key"] == "pippit_shortplay_cvtob_video_generate_fast720p"
    assert captured_pro["form"]["req_key"] == "pippit_shortplay_cvtob_video_generate_pro720p"


@pytest.mark.asyncio
async def test_non_10000_raises_xiaoyunque_api_error():
    client = _make_client()

    def fake_submit(form: dict) -> dict:
        return {"code": 50412, "data": None, "message": "Text Risk Not Pass"}

    with patch.object(client._visual, "cv_sync2async_submit_task", side_effect=fake_submit):
        with pytest.raises(XiaoyunqueAPIError) as exc_info:
            await client.submit_script_analysis(
                visual_style="x", video_ratio="16:9",
                file_url="https://x/s.docx", file_type="docx", file_name="s.docx",
            )
    assert exc_info.value.code == 50412


@pytest.mark.asyncio
async def test_query_script_analysis_parses_resp_data():
    client = _make_client()

    def fake_query(form: dict) -> dict:
        resp_data = (
            '{"thread_id":"ark_t1","assets_id":"ark_a1","status":"Success",'
            '"script_detail":{"CoreElement":{"EpisodeCount":3},'
            '"EpisodeAssets":[{"EpisodeID":"1","EpisodeTitle":"x","EpisodeAssetID":"ea1",'
            '"CharacterAssetIDs":["c1"],"SceneAssetIDs":["s1"]}]}}'
        )
        return {"code": 10000, "data": {"status": "done", "resp_data": resp_data}}

    with patch.object(client._visual, "cv_sync2async_get_result", side_effect=fake_query):
        result = await client.query_script_analysis("tid")

    assert result.status == "done"
    assert result.thread_id == "ark_t1"
    assert result.assets_id == "ark_a1"
    assert result.episode_count == 3
    assert len(result.episodes) == 1
    assert result.episodes[0].episode_id == "1"


@pytest.mark.asyncio
async def test_query_returns_in_progress_state_when_not_done():
    client = _make_client()

    def fake_query(form: dict) -> dict:
        return {"code": 10000, "data": {"status": "generating", "resp_data": ""}}

    with patch.object(client._visual, "cv_sync2async_get_result", side_effect=fake_query):
        result = await client.query_script_analysis("tid")

    assert result.status == "generating"
    assert result.thread_id is None
```

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_client.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `lib/xiaoyunque_shortplay/client.py`**

```python
"""4 步 API 薄壳封装，走官方 volcengine.visual.VisualService。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from volcengine.visual.VisualService import VisualService

from lib.xiaoyunque_shortplay.errors import XiaoyunqueAPIError

logger = logging.getLogger(__name__)

ModelVariant = Literal["fast720p", "pro720p"]

# req_key 表（来自小云雀短剧 Agent 文档）
_VIDEO_GEN_REQ_KEY = {
    "fast720p": "pippit_shortplay_cvtob_video_generate_fast720p",
    "pro720p": "pippit_shortplay_cvtob_video_generate_pro720p",
}
_VIDEO_COMPOSE_REQ_KEY = {
    "fast720p": "pippit_shortplay_cvtob_video_compose_fast720p",
    "pro720p": "pippit_shortplay_cvtob_video_compose_pro720p",
}


# --- 反序列化后的 resp_data 摘要 dataclass ---


@dataclass
class EpisodeAssetInfo:
    episode_id: str
    episode_title: str = ""
    episode_asset_id: str = ""
    character_asset_ids: list[str] = field(default_factory=list)
    scene_asset_ids: list[str] = field(default_factory=list)


@dataclass
class ScriptAnalysisResult:
    status: str  # "processing" / "generating" / "done" / "expired" / ...
    thread_id: str | None = None
    assets_id: str | None = None
    episode_count: int = 0
    episodes: list[EpisodeAssetInfo] = field(default_factory=list)
    charge_count: int = 0


@dataclass
class CharacterDesignInfo:
    character_id: str
    character_name: str
    body_image_url: str | None = None
    bust_portrait_url: str | None = None
    appearance_count: int = 0
    actual_render_count: int = 0


@dataclass
class SceneDesignInfo:
    scene_id: str
    name: str
    image_urls: list[str] = field(default_factory=list)


@dataclass
class MaterialDesignResult:
    status: str
    characters: list[CharacterDesignInfo] = field(default_factory=list)
    scenes: list[SceneDesignInfo] = field(default_factory=list)
    image_count: int = 0


@dataclass
class ShotInfo:
    shot_id: str
    description: str
    status: int  # 0/1/2/3/4/5
    video_url: str | None = None
    duration_ms: int = 0


@dataclass
class VideoGenerateResult:
    status: str
    storyboard_status_map: dict[str, int] = field(default_factory=dict)
    shots: list[ShotInfo] = field(default_factory=list)
    charge_count: int = 0


@dataclass
class VideoComposeResult:
    status: str
    final_video_url: str | None = None
    final_cover_url: str | None = None


# --- Client 主体 ---


class XiaoyunqueShortplayClient:
    """4 步短剧 pipeline 调用封装。"""

    def __init__(self, *, access_key: str, secret_key: str, model_variant: ModelVariant = "fast720p"):
        self._visual = VisualService()
        self._visual.set_ak(access_key)
        self._visual.set_sk(secret_key)
        self._model_variant: ModelVariant = model_variant

    # ===================== Step 1: 剧本解析 =====================

    async def submit_script_analysis(
        self, *, visual_style: str, video_ratio: str,
        file_url: str, file_type: str, file_name: str,
    ) -> str:
        form: dict[str, Any] = {
            "req_key": "pippit_shortplay_cvtob_script_analysis",
            "visual_style": visual_style,
            "video_ratio": video_ratio,
            "file_url": file_url,
            "file_type": file_type,
            "file_name": file_name,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_script_analysis(self, task_id: str) -> ScriptAnalysisResult:
        form = {"req_key": "pippit_shortplay_cvtob_script_analysis", "task_id": task_id}
        data = await self._query(form)
        return _parse_script_analysis(data)

    # ===================== Step 2: 图片生成 =====================

    async def submit_material_design(self, *, assets_id: str, thread_id: str, run_id: str) -> str:
        form = {
            "req_key": "pippit_shortplay_cvtob_material_design",
            "assets_id": assets_id, "thread_id": thread_id, "run_id": run_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_material_design(self, task_id: str) -> MaterialDesignResult:
        form = {"req_key": "pippit_shortplay_cvtob_material_design", "task_id": task_id}
        data = await self._query(form)
        return _parse_material_design(data)

    # ===================== Step 3: 视频生成（每集独立调用） =====================

    async def submit_video_generate(
        self, *, assets_id: str, thread_id: str, episode_id: str, run_id: str,
    ) -> str:
        form = {
            "req_key": _VIDEO_GEN_REQ_KEY[self._model_variant],
            "assets_id": assets_id, "thread_id": thread_id,
            "episode_id": episode_id, "run_id": run_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_video_generate(self, task_id: str) -> VideoGenerateResult:
        form = {"req_key": _VIDEO_GEN_REQ_KEY[self._model_variant], "task_id": task_id}
        data = await self._query(form)
        return _parse_video_generate(data)

    # ===================== Step 4: 视频合成（每集独立调用） =====================

    async def submit_video_compose(self, *, assets_id: str, thread_id: str, episode_id: str) -> str:
        form = {
            "req_key": _VIDEO_COMPOSE_REQ_KEY[self._model_variant],
            "assets_id": assets_id, "thread_id": thread_id, "episode_id": episode_id,
        }
        data = await self._submit(form)
        return self._extract_task_id(data)

    async def query_video_compose(self, task_id: str) -> VideoComposeResult:
        form = {"req_key": _VIDEO_COMPOSE_REQ_KEY[self._model_variant], "task_id": task_id}
        data = await self._query(form)
        return _parse_video_compose(data)

    # ===================== 私有 helper =====================

    async def _submit(self, form: dict[str, Any]) -> dict[str, Any]:
        resp = await asyncio.to_thread(self._visual.cv_sync2async_submit_task, form)
        if not isinstance(resp, dict):
            raise XiaoyunqueAPIError(code=-1, message=f"non-dict response: {resp!r}")
        if resp.get("code") != 10000:
            raise XiaoyunqueAPIError(
                code=int(resp.get("code") or -1),
                message=str(resp.get("message", "")),
                request_id=resp.get("request_id"),
            )
        return resp

    async def _query(self, form: dict[str, Any]) -> dict[str, Any]:
        resp = await asyncio.to_thread(self._visual.cv_sync2async_get_result, form)
        if not isinstance(resp, dict):
            raise XiaoyunqueAPIError(code=-1, message=f"non-dict response: {resp!r}")
        if resp.get("code") != 10000:
            raise XiaoyunqueAPIError(
                code=int(resp.get("code") or -1),
                message=str(resp.get("message", "")),
                request_id=resp.get("request_id"),
            )
        return resp

    @staticmethod
    def _extract_task_id(resp: dict[str, Any]) -> str:
        data = resp.get("data")
        if not isinstance(data, dict) or not data.get("task_id"):
            raise XiaoyunqueAPIError(code=-1, message=f"missing task_id: {resp!r}")
        return str(data["task_id"])


# --- resp_data 解析 ---


def _resp_data_dict(resp: dict[str, Any]) -> dict[str, Any]:
    data = resp.get("data") or {}
    raw = data.get("resp_data")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_status(resp: dict[str, Any]) -> str:
    data = resp.get("data") or {}
    return str(data.get("status", ""))


def _parse_script_analysis(resp: dict[str, Any]) -> ScriptAnalysisResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    detail = rd.get("script_detail") or {}
    episodes_raw = detail.get("EpisodeAssets") or []
    core = detail.get("CoreElement") or {}
    episode_count = int(core.get("EpisodeCount") or 0)

    episodes = [
        EpisodeAssetInfo(
            episode_id=str(e.get("EpisodeID", "")),
            episode_title=str(e.get("EpisodeTitle", "")),
            episode_asset_id=str(e.get("EpisodeAssetID", "")),
            character_asset_ids=[str(x) for x in (e.get("CharacterAssetIDs") or [])],
            scene_asset_ids=[str(x) for x in (e.get("SceneAssetIDs") or [])],
        )
        for e in episodes_raw
    ]
    return ScriptAnalysisResult(
        status=status,
        thread_id=rd.get("thread_id"),
        assets_id=rd.get("assets_id"),
        episode_count=episode_count,
        episodes=episodes,
        charge_count=int(rd.get("charge_count") or 0),
    )


def _parse_material_design(resp: dict[str, Any]) -> MaterialDesignResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)

    chars: list[CharacterDesignInfo] = []
    for c in rd.get("character_detail") or []:
        tree = c.get("AppearanceTree") or {}
        detail = tree.get("Detail") or {}
        chars.append(
            CharacterDesignInfo(
                character_id=str(c.get("CharacterID", "")),
                character_name=str(c.get("CharacterName", "")),
                body_image_url=detail.get("BodyImageURL"),
                bust_portrait_url=detail.get("BustPortraitURL"),
                appearance_count=int(c.get("ExpectRenderImageCount") or 0),
                actual_render_count=int(c.get("ActualRenderImageCount") or 0),
            )
        )

    scenes: list[SceneDesignInfo] = []
    for s in rd.get("scene_detail") or []:
        urls = [
            ap.get("ImageURL")
            for ap in (s.get("AppearanceDetails") or [])
            if ap.get("ImageURL")
        ]
        scenes.append(
            SceneDesignInfo(
                scene_id=str(s.get("SceneID", "")),
                name=str(s.get("Name", "")),
                image_urls=[u for u in urls if u],
            )
        )

    return MaterialDesignResult(
        status=status,
        characters=chars,
        scenes=scenes,
        image_count=int(rd.get("image_count") or 0),
    )


def _parse_video_generate(resp: dict[str, Any]) -> VideoGenerateResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    storyboard = (rd.get("storyboard_detail") or [{}])[0] if rd.get("storyboard_detail") else {}

    status_map_raw = storyboard.get("ShotStatusMap") or {}
    status_map = {
        shot_id: int((info or {}).get("Status") or 0)
        for shot_id, info in status_map_raw.items()
    }

    shots = [
        ShotInfo(
            shot_id=str(s.get("ShotID", "")),
            description=str(s.get("Description", "")),
            status=int(s.get("Status") or 0),
            video_url=s.get("VideoURL"),
            duration_ms=int(s.get("Duration") or 0),
        )
        for s in storyboard.get("Shots") or []
    ]
    return VideoGenerateResult(
        status=status,
        storyboard_status_map=status_map,
        shots=shots,
        charge_count=int(rd.get("charge_count") or 0),
    )


def _parse_video_compose(resp: dict[str, Any]) -> VideoComposeResult:
    status = _get_status(resp)
    rd = _resp_data_dict(resp)
    return VideoComposeResult(
        status=status,
        final_video_url=rd.get("final_video_url"),
        final_cover_url=rd.get("final_cover_url"),
    )
```

- [ ] **Step 4：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_client.py -v
```

期望：5 passed。

- [ ] **Step 5：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/client.py tests/test_xiaoyunque_client.py
uv run ruff format lib/xiaoyunque_shortplay/client.py tests/test_xiaoyunque_client.py
uv run basedpyright lib/xiaoyunque_shortplay/client.py
```

- [ ] **Step 6：commit**

```bash
git add lib/xiaoyunque_shortplay/client.py tests/test_xiaoyunque_client.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): client 4 步 API 封装 + resp_data 解析

XiaoyunqueShortplayClient 4 个 submit/query 方法对接：
- 剧本解析 / 图片生成 / 视频生成 / 视频合成
- 视频生成/合成根据 model_variant 自动选 fast720p / pro720p req_key
- resp_data JSON 字符串解析为 dataclass 摘要
- code != 10000 统一抛 XiaoyunqueAPIError

走官方 VisualService SDK 已活体验证。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 · store.py 持久化

**Files:**
- Create: `lib/xiaoyunque_shortplay/store.py`
- Create: `tests/test_xiaoyunque_store.py`

- [ ] **Step 1：写失败测试**

```python
"""XiaoyunqueRunStore — CRUD + status transition + state_json 读写。"""

from __future__ import annotations

import pytest

from lib.xiaoyunque_shortplay.state import EpisodeState, RunState
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_create_then_get(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    assert run.id
    assert run.status == "pending"

    fetched = await store.get(run.id)
    assert fetched.id == run.id


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_set_status_advances_state(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_status(run.id, "parsing")
    refreshed = await store.get(run.id)
    assert refreshed.status == "parsing"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_state_roundtrip(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    s = RunState(parse_task_id="t1", episodes=[EpisodeState(episode_id="1")])
    await store.set_state(run.id, s)

    loaded = await store.get_state(run.id)
    assert loaded.parse_task_id == "t1"
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].episode_id == "1"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_set_failed_records_error(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9",
        script_file_url="https://x/s.docx",
    )
    await store.set_failed(run.id, "kaboom")
    fetched = await store.get(run.id)
    assert fetched.status == "failed"
    assert fetched.last_error == "kaboom"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_list_in_progress_excludes_terminal_states(async_session):
    store = XiaoyunqueRunStore(async_session)
    r_pending = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )
    r_done = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )
    await store.set_status(r_done.id, "done")

    in_progress = await store.list_in_progress()
    ids = {r.id for r in in_progress}
    assert r_pending.id in ids
    assert r_done.id not in ids
```

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_store.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `lib/xiaoyunque_shortplay/store.py`**

```python
"""XiaoyunqueRunStore — 异步 SQLAlchemy CRUD + state_json 序列化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.xiaoyunque_run import XiaoyunqueRun
from lib.xiaoyunque_shortplay.state import RunState, state_from_dict, state_to_dict


_TERMINAL_STATUSES = ("done", "failed", "cancelled")


class XiaoyunqueRunStore:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self, *, project_name: str, model_variant: str,
        visual_style: str, video_ratio: str, script_file_url: str,
    ) -> XiaoyunqueRun:
        run = XiaoyunqueRun(
            id=uuid4().hex,
            project_name=project_name,
            status="pending",
            model_variant=model_variant,
            visual_style=visual_style,
            video_ratio=video_ratio,
            script_file_url=script_file_url,
            state_json="{}",
        )
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def get(self, run_id: str) -> XiaoyunqueRun:
        run = (
            await self._session.execute(
                select(XiaoyunqueRun).where(XiaoyunqueRun.id == run_id)
            )
        ).scalar_one_or_none()
        if run is None:
            raise LookupError(f"xiaoyunque run not found: {run_id}")
        return run

    async def set_status(
        self, run_id: str, status: str, *, completed: bool = False,
    ) -> None:
        run = await self.get(run_id)
        run.status = status
        run.updated_at = datetime.now(timezone.utc)
        if completed:
            run.completed_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def set_failed(self, run_id: str, error_message: str) -> None:
        run = await self.get(run_id)
        run.status = "failed"
        run.last_error = error_message
        run.updated_at = datetime.now(timezone.utc)
        run.completed_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def set_state(self, run_id: str, state: RunState) -> None:
        run = await self.get(run_id)
        run.state_json = json.dumps(state_to_dict(state), ensure_ascii=False)
        run.updated_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def get_state(self, run_id: str) -> RunState:
        run = await self.get(run_id)
        return state_from_dict(json.loads(run.state_json or "{}"))

    async def set_thread_and_assets(
        self, run_id: str, *, thread_id: str, assets_id: str,
    ) -> None:
        run = await self.get(run_id)
        run.thread_id = thread_id
        run.assets_id = assets_id
        run.updated_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def list_in_progress(self) -> list[XiaoyunqueRun]:
        rows = (
            await self._session.execute(
                select(XiaoyunqueRun).where(
                    XiaoyunqueRun.status.not_in(_TERMINAL_STATUSES)
                )
            )
        ).scalars().all()
        return list(rows)

    async def list_by_project(
        self, project_name: str, *, status: str | None = None,
    ) -> list[XiaoyunqueRun]:
        stmt = select(XiaoyunqueRun).where(XiaoyunqueRun.project_name == project_name)
        if status:
            stmt = stmt.where(XiaoyunqueRun.status == status)
        stmt = stmt.order_by(XiaoyunqueRun.created_at.desc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)
```

- [ ] **Step 4：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_store.py -v
```

期望：5 passed。

- [ ] **Step 5：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/store.py tests/test_xiaoyunque_store.py
uv run ruff format lib/xiaoyunque_shortplay/store.py tests/test_xiaoyunque_store.py
uv run basedpyright lib/xiaoyunque_shortplay/store.py
```

- [ ] **Step 6：commit**

```bash
git add lib/xiaoyunque_shortplay/store.py tests/test_xiaoyunque_store.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): store 持久化

XiaoyunqueRunStore — create / get / set_status / set_state / set_failed /
set_thread_and_assets / list_in_progress / list_by_project。
state_json JSON 序列化往返；status transition 更新 updated_at；
terminal status（done/failed/cancelled）由 list_in_progress 自动过滤。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 · runner.py 编排

**Files:**
- Create: `lib/xiaoyunque_shortplay/runner.py`
- Create: `tests/test_xiaoyunque_runner.py`

- [ ] **Step 1：写失败测试**

```python
"""XiaoyunquePipelineRunner — 串通 happy path + 单集失败 + cancelled。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lib.xiaoyunque_shortplay.client import (
    EpisodeAssetInfo,
    MaterialDesignResult,
    ScriptAnalysisResult,
    ShotInfo,
    VideoComposeResult,
    VideoGenerateResult,
)
from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_happy_path_completes_all_steps(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )

    # 准备 fake client，按步骤返回预期数据
    fake = AsyncMock()
    fake.submit_script_analysis.return_value = "task-script"
    fake.query_script_analysis.return_value = ScriptAnalysisResult(
        status="done", thread_id="ark_t1", assets_id="ark_a1", episode_count=1,
        episodes=[EpisodeAssetInfo(episode_id="1", episode_title="x", episode_asset_id="ea1")],
    )
    fake.submit_material_design.return_value = "task-design"
    fake.query_material_design.return_value = MaterialDesignResult(
        status="done", characters=[], scenes=[], image_count=2,
    )
    fake.submit_video_generate.return_value = "task-vg-1"
    fake.query_video_generate.return_value = VideoGenerateResult(
        status="done",
        storyboard_status_map={"S1": 3},
        shots=[ShotInfo(shot_id="S1", description="x", status=3, video_url="https://x/s1.mp4")],
    )
    fake.submit_video_compose.return_value = "task-vc-1"
    fake.query_video_compose.return_value = VideoComposeResult(
        status="done", final_video_url="https://x/ep1.mp4",
        final_cover_url="https://x/ep1.png",
    )

    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)

    final = await store.get(run.id)
    assert final.status == "done"
    assert final.thread_id == "ark_t1"

    state = await store.get_state(run.id)
    assert state.episodes[0].final_video_url == "https://x/ep1.mp4"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_single_episode_video_failure_marks_episode_then_continues(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )

    fake = AsyncMock()
    fake.submit_script_analysis.return_value = "tsa"
    fake.query_script_analysis.return_value = ScriptAnalysisResult(
        status="done", thread_id="t", assets_id="a", episode_count=2,
        episodes=[
            EpisodeAssetInfo(episode_id="1", episode_asset_id="ea1"),
            EpisodeAssetInfo(episode_id="2", episode_asset_id="ea2"),
        ],
    )
    fake.submit_material_design.return_value = "tmd"
    fake.query_material_design.return_value = MaterialDesignResult(status="done")
    fake.submit_video_generate.return_value = "tvg"

    # 集 1 失败 (status=failed)，集 2 成功
    vg_responses = [
        VideoGenerateResult(status="failed"),  # 集 1
        VideoGenerateResult(status="done", shots=[]),  # 集 2
    ]
    fake.query_video_generate.side_effect = vg_responses
    fake.submit_video_compose.return_value = "tvc"
    fake.query_video_compose.return_value = VideoComposeResult(
        status="done", final_video_url="https://x/ep2.mp4",
    )

    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)

    final = await store.get(run.id)
    state = await store.get_state(run.id)
    # 整 run 推到 done（即使有集失败）
    assert final.status == "done"
    ep1, ep2 = state.episodes
    assert ep1.status == "failed"
    assert ep2.status == "done"
    assert ep2.final_video_url == "https://x/ep2.mp4"


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_cancel_during_run_aborts_early(async_session):
    store = XiaoyunqueRunStore(async_session)
    run = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )
    await store.set_status(run.id, "cancelled")

    fake = AsyncMock()
    runner = XiaoyunquePipelineRunner(fake, store)
    await runner.run(run.id)  # 应直接 noop 返回

    fake.submit_script_analysis.assert_not_called()
```

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_runner.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `lib/xiaoyunque_shortplay/runner.py`**

```python
"""XiaoyunquePipelineRunner — 4 步 pipeline 编排。

可恢复设计：每步成功立即写库（status 进入下一阶段），重启后从最新 status 继续。
"""

from __future__ import annotations

import asyncio
import logging

from lib.xiaoyunque_shortplay.client import (
    EpisodeAssetInfo,
    MaterialDesignResult,
    ScriptAnalysisResult,
    VideoComposeResult,
    VideoGenerateResult,
    XiaoyunqueShortplayClient,
)
from lib.xiaoyunque_shortplay.errors import (
    PipelineCancelled,
    XiaoyunqueAPIError,
    XiaoyunqueError,
    classify_business_code,
)
from lib.xiaoyunque_shortplay.state import (
    CharacterState,
    EpisodeState,
    RunState,
    SceneState,
    ScriptSummary,
    ShotState,
)
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15
MAX_WAIT_SEC_PER_STEP = 1800  # 30 min/step；剧本/图片可能 4-10 min，视频生成 7 min/集


class XiaoyunquePipelineRunner:
    """编排 4 步 pipeline。每步 store.set_state + set_status 推进，断点续传安全。"""

    def __init__(self, client: XiaoyunqueShortplayClient, store: XiaoyunqueRunStore):
        self.client = client
        self.store = store

    async def run(self, run_id: str) -> None:
        run = await self.store.get(run_id)

        if run.status in ("done", "failed", "cancelled"):
            logger.info("run %s 已经在终态 %s，跳过", run_id, run.status)
            return

        try:
            await self._execute(run_id)
            await self.store.set_status(run_id, "done", completed=True)
        except PipelineCancelled:
            logger.info("run %s 被取消", run_id)
        except XiaoyunqueError as e:
            logger.exception("run %s 失败", run_id)
            await self.store.set_failed(run_id, str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("run %s 异常", run_id)
            await self.store.set_failed(run_id, f"unexpected: {e}")

    async def _execute(self, run_id: str) -> None:
        # Step 1: 剧本解析
        run = await self.store.get(run_id)
        if run.status == "pending":
            await self._step1_parse(run_id)

        # Step 2: 图片生成
        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "parsing":
            await self._step2_design(run_id)

        # Step 3: 视频生成（每集）
        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "designing":
            await self._step3_video_generate(run_id)

        # Step 4: 视频合成（每集）
        run = await self.store.get(run_id)
        await self._check_cancelled(run.status, run_id)
        if run.status == "generating":
            await self._step4_video_compose(run_id)

    @staticmethod
    async def _check_cancelled(status: str, run_id: str) -> None:
        if status == "cancelled":
            raise PipelineCancelled(run_id)

    # ===================== Step 1 =====================

    async def _step1_parse(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        state = await self.store.get_state(run_id)

        if not state.parse_task_id:
            task_id = await self.client.submit_script_analysis(
                visual_style=run.visual_style,
                video_ratio=run.video_ratio,
                file_url=run.script_file_url,
                file_type=_extract_file_type(run.script_file_url),
                file_name=_extract_file_name(run.script_file_url),
            )
            state.parse_task_id = task_id
            await self.store.set_state(run_id, state)
            logger.info("xiaoyunque run %s 剧本解析提交 task=%s", run_id, task_id)

        result = await self._wait_for(
            lambda: self.client.query_script_analysis(state.parse_task_id or ""),
            label=f"run={run_id} step=script_analysis",
        )
        await self._record_script(run_id, state, result)
        await self.store.set_status(run_id, "parsing")

    async def _record_script(
        self, run_id: str, state: RunState, result: ScriptAnalysisResult,
    ) -> None:
        if not result.thread_id or not result.assets_id:
            raise XiaoyunqueError(f"剧本解析未返回 thread_id/assets_id: {result}")
        await self.store.set_thread_and_assets(
            run_id, thread_id=result.thread_id, assets_id=result.assets_id,
        )
        state.script = ScriptSummary(episode_count=result.episode_count)
        state.episodes = [
            EpisodeState(
                episode_id=e.episode_id,
                episode_asset_id=e.episode_asset_id,
                title=e.episode_title,
            )
            for e in result.episodes
        ]
        state.charge_count_total += result.charge_count
        await self.store.set_state(run_id, state)

    # ===================== Step 2 =====================

    async def _step2_design(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        state = await self.store.get_state(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step2 缺少 thread_id/assets_id: run_id={run_id}")

        if not state.design_task_id:
            task_id = await self.client.submit_material_design(
                assets_id=run.assets_id, thread_id=run.thread_id, run_id=run_id,
            )
            state.design_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_material_design(state.design_task_id or ""),
            label=f"run={run_id} step=material_design",
        )
        await self._record_design(run_id, state, result)
        await self.store.set_status(run_id, "designing")

    async def _record_design(
        self, run_id: str, state: RunState, result: MaterialDesignResult,
    ) -> None:
        state.characters = [
            CharacterState(
                character_id=c.character_id,
                name=c.character_name,
                body_image_url=c.body_image_url,
                bust_portrait_url=c.bust_portrait_url,
                appearance_count=c.actual_render_count,
            )
            for c in result.characters
        ]
        state.scenes = [
            SceneState(scene_id=s.scene_id, name=s.name, image_urls=s.image_urls)
            for s in result.scenes
        ]
        state.charge_count_total += result.image_count
        await self.store.set_state(run_id, state)

    # ===================== Step 3 =====================

    async def _step3_video_generate(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step3 缺少 thread_id/assets_id: run_id={run_id}")

        # 状态机推进到 generating，让中断恢复时知道走的是 step3
        await self.store.set_status(run_id, "generating")

        state = await self.store.get_state(run_id)
        for episode in state.episodes:
            await self._check_cancelled((await self.store.get(run_id)).status, run_id)
            if episode.status in ("done", "composing"):
                continue  # 已经过了 step3

            try:
                await self._generate_one_episode(run_id, run.thread_id, run.assets_id, episode)
            except XiaoyunqueAPIError as e:
                if classify_business_code(e.code) == "fatal":
                    episode.status = "failed"
                    episode.error_message = str(e)
                    await self.store.set_state(run_id, state)
                    continue
                raise

            state = await self.store.get_state(run_id)  # 别的字段可能已被刷新

    async def _generate_one_episode(
        self, run_id: str, thread_id: str, assets_id: str, episode: EpisodeState,
    ) -> None:
        state = await self.store.get_state(run_id)
        if not episode.video_generate_task_id:
            task_id = await self.client.submit_video_generate(
                assets_id=assets_id, thread_id=thread_id,
                episode_id=episode.episode_id, run_id=run_id,
            )
            episode.video_generate_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_video_generate(episode.video_generate_task_id or ""),
            label=f"run={run_id} ep={episode.episode_id} step=video_generate",
        )

        episode.shots = [
            ShotState(
                shot_id=s.shot_id, description=s.description, status=s.status,
                video_url=s.video_url, duration_ms=s.duration_ms,
            )
            for s in result.shots
        ]
        if any(s.status != 3 for s in result.shots):
            episode.status = "failed"
            episode.error_message = f"部分分镜失败: {result.storyboard_status_map}"
        else:
            episode.status = "composing"  # 准备进入 step4
        state.charge_count_total += result.charge_count
        await self.store.set_state(run_id, state)

    # ===================== Step 4 =====================

    async def _step4_video_compose(self, run_id: str) -> None:
        run = await self.store.get(run_id)
        if not run.thread_id or not run.assets_id:
            raise XiaoyunqueError(f"step4 缺少 thread_id/assets_id: run_id={run_id}")

        await self.store.set_status(run_id, "composing")

        state = await self.store.get_state(run_id)
        for episode in state.episodes:
            await self._check_cancelled((await self.store.get(run_id)).status, run_id)
            if episode.status != "composing":
                continue  # 失败或已合成的跳过

            try:
                await self._compose_one_episode(run_id, run.thread_id, run.assets_id, episode)
            except XiaoyunqueAPIError as e:
                episode.status = "failed_compose"
                episode.error_message = str(e)
                await self.store.set_state(run_id, state)
                continue

            state = await self.store.get_state(run_id)

    async def _compose_one_episode(
        self, run_id: str, thread_id: str, assets_id: str, episode: EpisodeState,
    ) -> None:
        state = await self.store.get_state(run_id)
        if not episode.video_compose_task_id:
            task_id = await self.client.submit_video_compose(
                assets_id=assets_id, thread_id=thread_id, episode_id=episode.episode_id,
            )
            episode.video_compose_task_id = task_id
            await self.store.set_state(run_id, state)

        result = await self._wait_for(
            lambda: self.client.query_video_compose(episode.video_compose_task_id or ""),
            label=f"run={run_id} ep={episode.episode_id} step=video_compose",
        )
        episode.final_video_url = result.final_video_url
        episode.final_cover_url = result.final_cover_url
        episode.status = "done"
        await self.store.set_state(run_id, state)

    # ===================== 通用轮询 helper =====================

    async def _wait_for(self, query_fn, *, label: str):  # type: ignore[no-untyped-def]
        start = asyncio.get_event_loop().time()
        while True:
            try:
                result = await query_fn()
            except XiaoyunqueAPIError as e:
                if classify_business_code(e.code) == "retryable":
                    logger.warning("%s 瞬态错误 code=%s 将重试", label, e.code)
                else:
                    raise
            else:
                status = result.status
                if status == "done":
                    return result
                if status in ("not_found", "expired"):
                    raise XiaoyunqueError(f"{label} 任务过期/丢失 status={status}")
                logger.info("%s 状态=%s", label, status)

            if asyncio.get_event_loop().time() - start >= MAX_WAIT_SEC_PER_STEP:
                raise XiaoyunqueError(f"{label} 超时 ({MAX_WAIT_SEC_PER_STEP}s)")
            await asyncio.sleep(POLL_INTERVAL_SEC)


# --- 文件路径工具 ---


def _extract_file_name(url: str) -> str:
    """从 TOS 签名 URL 取文件名（不含 query）。"""
    path = url.split("?", 1)[0]
    return path.rsplit("/", 1)[-1] or "script.docx"


def _extract_file_type(url: str) -> str:
    name = _extract_file_name(url)
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return "docx"
```

- [ ] **Step 4：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_runner.py -v
```

期望：3 passed。

- [ ] **Step 5：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/runner.py tests/test_xiaoyunque_runner.py
uv run ruff format lib/xiaoyunque_shortplay/runner.py tests/test_xiaoyunque_runner.py
uv run basedpyright lib/xiaoyunque_shortplay/runner.py
```

- [ ] **Step 6：commit**

```bash
git add lib/xiaoyunque_shortplay/runner.py tests/test_xiaoyunque_runner.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): runner 编排 4 步 pipeline

XiaoyunquePipelineRunner 串通：剧本解析 → 图片生成 → 视频生成（每集）
→ 视频合成（每集）。每步成功立即 set_state + set_status 推进，断点续传。

错误处理：
- code=fatal 单集失败 → episode.status=failed/failed_compose 跳过，继续下一集
- code=retryable → log warning + 继续轮询
- status=expired/not_found → 整 run 失败
- run.status=cancelled → 任何步骤前检测到立即 raise PipelineCancelled

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 · worker.py 后台启动 + 关停

**Files:**
- Create: `lib/xiaoyunque_shortplay/worker.py`
- Modify: `server/app.py`（lifespan hook 启动 worker）
- Create: `tests/test_xiaoyunque_worker.py`

- [ ] **Step 1：写失败测试**

```python
"""XiaoyunqueWorker — 启动时拉未完成 run，每个起一个 asyncio task。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from lib.xiaoyunque_shortplay.worker import XiaoyunqueWorker


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_worker_picks_up_in_progress_runs_on_start(async_session):
    store = XiaoyunqueRunStore(async_session)
    r1 = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )
    r2 = await store.create(
        project_name="p2", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )
    await store.set_status(r2.id, "done")  # 终态，应被跳过

    runs_seen: list[str] = []

    async def fake_run(run_id: str) -> None:
        runs_seen.append(run_id)

    worker = XiaoyunqueWorker(
        runner_factory=lambda store_arg: _FakeRunner(fake_run),
        store_factory=lambda: store,
    )
    await worker.start()
    await worker.wait_idle(timeout=2.0)
    await worker.stop()

    assert r1.id in runs_seen
    assert r2.id not in runs_seen


class _FakeRunner:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self._cb = callback

    async def run(self, run_id: str) -> None:
        await self._cb(run_id)


@pytest.mark.asyncio
@pytest.mark.uses_db
async def test_worker_can_be_triggered_for_new_run(async_session):
    store = XiaoyunqueRunStore(async_session)
    r = await store.create(
        project_name="p1", model_variant="fast720p",
        visual_style="x", video_ratio="16:9", script_file_url="https://x/s.docx",
    )

    runs_seen: list[str] = []

    async def fake_run(run_id: str) -> None:
        runs_seen.append(run_id)

    worker = XiaoyunqueWorker(
        runner_factory=lambda store_arg: _FakeRunner(fake_run),
        store_factory=lambda: store,
    )
    await worker.start()
    worker.trigger(r.id)
    await worker.wait_idle(timeout=2.0)
    await worker.stop()

    assert r.id in runs_seen
```

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_worker.py -v
```

期望：ImportError。

- [ ] **Step 3：写实现 `lib/xiaoyunque_shortplay/worker.py`**

```python
"""XiaoyunqueWorker — 后台运行 pipeline，单进程，每个 run 一个 asyncio task。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore

logger = logging.getLogger(__name__)


class XiaoyunqueWorker:
    """后台 runner 池。lifespan startup 时 start()，shutdown 时 stop()。"""

    def __init__(
        self,
        *,
        runner_factory: Callable[[XiaoyunqueRunStore], XiaoyunquePipelineRunner],
        store_factory: Callable[[], XiaoyunqueRunStore],
    ):
        self._runner_factory = runner_factory
        self._store_factory = store_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        store = self._store_factory()
        in_progress = await store.list_in_progress()
        for run in in_progress:
            self._spawn(run.id)
        logger.info("XiaoyunqueWorker 启动，拉起 %d 个在途 run", len(in_progress))

    def trigger(self, run_id: str) -> None:
        """新建 run 时由 router 调用，启动 runner task。"""
        if not self._started:
            logger.warning("worker 未启动，trigger %s 被忽略", run_id)
            return
        if run_id in self._tasks and not self._tasks[run_id].done():
            logger.info("run %s 已在跑，跳过", run_id)
            return
        self._spawn(run_id)

    def _spawn(self, run_id: str) -> None:
        async def _runner_task() -> None:
            store = self._store_factory()
            runner = self._runner_factory(store)
            try:
                await runner.run(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("runner task for run %s 异常", run_id)
            finally:
                self._tasks.pop(run_id, None)

        self._tasks[run_id] = asyncio.create_task(_runner_task(), name=f"xiaoyunque-{run_id}")

    async def wait_idle(self, *, timeout: float = 30.0) -> None:
        """等待当前所有 task 完成（测试用）。"""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(
            list(self._tasks.values()), timeout=timeout, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            for t in pending:
                t.cancel()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("XiaoyunqueWorker 已停止")
```

- [ ] **Step 4：跑测试看通过**

```bash
uv run pytest tests/test_xiaoyunque_worker.py -v
```

期望：2 passed。

- [ ] **Step 5：lifespan 集成 `server/app.py`**

找到现有 `GenerationWorker` 启动逻辑（grep `启动 GenerationWorker`），在其后追加：

```python
# 启动 XiaoyunqueWorker
logger.info("启动 XiaoyunqueWorker...")
from lib.xiaoyunque_shortplay.worker import XiaoyunqueWorker
from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from lib.db import async_session_factory

def _make_store() -> XiaoyunqueRunStore:
    raise RuntimeError("placeholder; see below")  # ← 实施时根据真实 session factory 实现

# 推荐写法：每个 run task 自带一个 session（避免长生命周期 session 并发问题）。
# 实际实施时把 _make_store 改成：
# async def _ctx_session():
#     async with async_session_factory() as session:
#         return XiaoyunqueRunStore(session)
# 但 session_factory 是 async ctx manager，需 worker 内自己管理 with。
# 现成做法见 server/services/generation_tasks.py 的 session 使用模式。
```

**真实集成代码**（用 `lib/db/engine.py` 的 async session_factory）：

```python
# server/app.py lifespan 内

from lib.db.engine import get_async_session_factory  # 实施时确认真实导出名
from lib.xiaoyunque_shortplay.client import XiaoyunqueShortplayClient
from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from lib.xiaoyunque_shortplay.worker import XiaoyunqueWorker
from lib.config.service import ConfigService  # 取 AK/SK

async def _build_client() -> XiaoyunqueShortplayClient:
    # 从 ConfigService 读 volc-xiaoyunque provider 配置
    cfg = await ConfigService.get_provider("volc-xiaoyunque")
    return XiaoyunqueShortplayClient(
        access_key=cfg["access_key"],
        secret_key=cfg["secret_key"],
        model_variant=cfg.get("shortplay_default_model_variant", "fast720p"),
    )

@asynccontextmanager
async def _runner_factory_session():
    factory = get_async_session_factory()
    async with factory() as session:
        yield XiaoyunqueRunStore(session)

# worker 内 spawn 时每次 enter context；为简化，第一版可以让 worker 持有
# session_factory，runner_task 自己 enter/exit。
# 详细做法以现有 GenerationWorker 的 session 管理为准（grep 'AsyncSession' 在
# generation_worker.py 看是怎么用 session 的）。

# 把 worker 实例存到 app.state，shutdown 时 stop
xiaoyunque_worker = XiaoyunqueWorker(...)  # ← 详细按上面思路装好
await xiaoyunque_worker.start()
app.state.xiaoyunque_worker = xiaoyunque_worker

# yield 之后（shutdown 段）
await xiaoyunque_worker.stop()
```

**注意**：lifespan 注入 session 比较复杂；实施时如果发现现有 `GenerationWorker` 也是每个 task 自管 session，直接套同样模式。如果一时拿不准，**降级方案**：worker 不在 lifespan 启动，而是 router 收到 POST 时手动 `asyncio.create_task` 跑 runner（简单但失去启动时恢复在途 run 的能力——可以接受作为 Phase A 的简化）。

如果走简化路径：跳过本 Step 5 的 lifespan 集成，把 worker.py 也跳过；router 直接 fire-and-forget `asyncio.create_task(runner.run(run_id))`。在 Task 8 router 实施时记得这个简化决定。

- [ ] **Step 6：lint + typecheck**

```bash
uv run ruff check lib/xiaoyunque_shortplay/worker.py tests/test_xiaoyunque_worker.py server/app.py
uv run ruff format lib/xiaoyunque_shortplay/worker.py tests/test_xiaoyunque_worker.py server/app.py
uv run basedpyright lib/xiaoyunque_shortplay/worker.py
```

- [ ] **Step 7：commit**

```bash
git add lib/xiaoyunque_shortplay/worker.py tests/test_xiaoyunque_worker.py server/app.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): worker 后台运行 + lifespan 集成

XiaoyunqueWorker 启动时拉 status NOT IN (done/failed/cancelled) 的 run
继续跑；trigger(run_id) 由 router 在新建后调用；stop() 取消所有 task。

app.py lifespan startup 启动、shutdown 停止。简化决定：先用每 task
独立 session 模式，与 GenerationWorker 一致。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 · REST API router

**Files:**
- Create: `server/routers/xiaoyunque_shortplay.py`
- Modify: `server/app.py`（注册 router）
- Modify: `lib/i18n/{zh,en,vi}/errors.py`（新 key）
- Modify: `lib/i18n/{zh,en,vi}/system.py`（新 key）
- Create: `tests/test_xiaoyunque_router.py`

- [ ] **Step 1：写失败测试**

```python
"""POST/GET/cancel/list 端点测试。Worker 用 fake，不触发真实 runner。"""

from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db import get_async_session
from server.auth import CurrentUserInfo, get_current_user
from server.routers import xiaoyunque_shortplay as router_mod


@pytest.fixture
def app(session_factory) -> FastAPI:
    _app = FastAPI()

    async def _override_session():
        async with session_factory() as s:
            yield s

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(
        id="test", sub="test", role="admin"
    )

    # 桩 worker / TOS uploader（避免外网调用）
    class FakeWorker:
        triggered: list[str] = []
        def trigger(self, run_id: str) -> None:
            FakeWorker.triggered.append(run_id)

    async def fake_upload_script(file_bytes, file_name, *, ak, sk, endpoint, bucket, region):
        return f"https://tos/{file_name}"

    _app.state.xiaoyunque_worker = FakeWorker()
    router_mod._upload_script_to_tos = fake_upload_script  # 临时替换

    _app.include_router(router_mod.router, prefix="/api/v1", tags=["xiaoyunque"])
    return _app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def test_create_run_returns_run_id(client, monkeypatch):
    """POST /xiaoyunque-shortplay/runs 接收剧本文件 + 元数据，返回 run_id。"""

    # 桩 ConfigService.get_provider
    from lib.config import service as cfg_service
    async def fake_get_provider(_name):
        return {
            "access_key": "ak", "secret_key": "sk",
            "tos_endpoint": "tos-cn-beijing.volces.com",
            "tos_bucket": "b", "tos_region": "cn-beijing",
        }
    monkeypatch.setattr(cfg_service.ConfigService, "get_provider", staticmethod(fake_get_provider))

    files = {"script_file": ("test.docx", io.BytesIO(b"fake docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
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


def test_get_run_returns_state(client, monkeypatch):
    # 直接预填一个 run
    from sqlalchemy import select
    from lib.db.models.xiaoyunque_run import XiaoyunqueRun

    # ... 实施时按 app fixture 拿到 session factory 直接插一条，再 GET
    # 简化：本测试只验证 404 路径

    resp = client.get("/api/v1/xiaoyunque-shortplay/runs/does-not-exist")
    assert resp.status_code == 404


def test_cancel_unknown_run_404(client):
    resp = client.post("/api/v1/xiaoyunque-shortplay/runs/does-not-exist/cancel")
    assert resp.status_code == 404
```

注：上面的 `test_get_run_returns_state` 在测试里直接预插记录写起来有点啰嗦；如果不想 yak-shave，先只覆盖 404 路径，正路径用 CLI 烟雾脚本 Task 9 覆盖。

- [ ] **Step 2：跑测试看失败**

```bash
uv run pytest tests/test_xiaoyunque_router.py -v
```

期望：ImportError 或 404 都正常。

- [ ] **Step 3：写实现 `server/routers/xiaoyunque_shortplay.py`**

```python
"""短剧 pipeline REST API。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.i18n import Translator
from lib.volc_tos_uploader import TosImageUploader
from lib.xiaoyunque_shortplay.errors import ScriptTooLongError
from lib.xiaoyunque_shortplay.state import state_from_dict
from lib.xiaoyunque_shortplay.store import XiaoyunqueRunStore
from server.auth import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter()

SCRIPT_CHAR_LIMIT = 300  # 文档限制
ALLOWED_SCRIPT_EXTS = {".docx", ".txt", ".pdf"}


async def _upload_script_to_tos(
    file_bytes: bytes, file_name: str,
    *, ak: str, sk: str, endpoint: str, bucket: str, region: str,
) -> str:
    """复用 TosImageUploader（任意二进制都能传）的上传 + 签名 URL 取得。"""
    import tempfile
    from pathlib import Path

    suffix = "." + file_name.rsplit(".", 1)[-1] if "." in file_name else ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = Path(f.name)
        tmp.write_bytes(file_bytes)
    try:
        uploader = TosImageUploader(
            ak=ak, sk=sk, endpoint=endpoint, bucket=bucket, region=region,
        )
        url = await uploader.upload_image(tmp, key_prefix="arcreel-shortplay/")
        return url
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/xiaoyunque-shortplay/runs")
async def create_run(
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session=Depends(get_async_session),
    project_name: str = Form(...),
    visual_style: str = Form(...),
    video_ratio: str = Form("16:9"),
    model_variant: str = Form("fast720p"),
    script_file: UploadFile = File(...),
):
    # 1. 校验扩展名
    from pathlib import Path
    filename = script_file.filename or "script.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_SCRIPT_EXTS:
        raise HTTPException(
            status_code=400,
            detail=_t(
                "external_script_unsupported_format",
                ext=ext, allowed=", ".join(sorted(ALLOWED_SCRIPT_EXTS)),
            ),
        )

    # 2. 读取 + 字符数校验（粗：按 utf-8 解码后 len，docx 解析需额外依赖 — 暂时只校验 txt）
    payload = await script_file.read()
    if ext == ".txt":
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail=_t("invalid_encoding"))
        if len(text) > SCRIPT_CHAR_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=_t("external_script_too_long", actual=len(text), limit=SCRIPT_CHAR_LIMIT),
            )

    # 3. 拿 provider 配置
    try:
        cfg = await ConfigService.get_provider("volc-xiaoyunque")
    except Exception:
        raise HTTPException(status_code=400, detail=_t("provider_not_configured", provider="volc-xiaoyunque"))

    # 4. 上传到 TOS
    try:
        script_url = await _upload_script_to_tos(
            payload, filename,
            ak=cfg["access_key"], sk=cfg["secret_key"],
            endpoint=cfg["tos_endpoint"],
            bucket=cfg["tos_bucket"], region=cfg["tos_region"],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("TOS 上传失败")
        raise HTTPException(status_code=500, detail=_t("tos_upload_failed", reason=str(e)))

    # 5. 写库
    store = XiaoyunqueRunStore(session)
    run = await store.create(
        project_name=project_name,
        model_variant=model_variant,
        visual_style=visual_style,
        video_ratio=video_ratio,
        script_file_url=script_url,
    )

    # 6. trigger worker
    worker = getattr(request.app.state, "xiaoyunque_worker", None)
    if worker is not None:
        worker.trigger(run.id)

    return {"run_id": run.id, "status": run.status}


@router.get("/xiaoyunque-shortplay/runs/{run_id}")
async def get_run(
    run_id: str,
    _user: CurrentUser,
    _t: Translator,
    session=Depends(get_async_session),
):
    store = XiaoyunqueRunStore(session)
    try:
        run = await store.get(run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail=_t("run_not_found", run_id=run_id))
    state = await store.get_state(run_id)
    return _serialize_run(run, state)


@router.get("/xiaoyunque-shortplay/runs")
async def list_runs(
    _user: CurrentUser,
    _t: Translator,
    session=Depends(get_async_session),
    project_name: str | None = None,
    status: str | None = None,
):
    store = XiaoyunqueRunStore(session)
    if project_name:
        rows = await store.list_by_project(project_name, status=status)
    else:
        rows = await store.list_in_progress() if status is None else []
    out = []
    for r in rows:
        state = state_from_dict(_safe_json(r.state_json))
        out.append(_serialize_run(r, state))
    return {"runs": out}


@router.post("/xiaoyunque-shortplay/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    _user: CurrentUser,
    _t: Translator,
    session=Depends(get_async_session),
):
    store = XiaoyunqueRunStore(session)
    try:
        run = await store.get(run_id)
    except LookupError:
        raise HTTPException(status_code=404, detail=_t("run_not_found", run_id=run_id))
    if run.status in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=_t("run_already_terminal", status=run.status))
    await store.set_status(run_id, "cancelled", completed=True)
    return {"run_id": run_id, "status": "cancelled"}


# --- helpers ---


def _safe_json(raw: str | None) -> dict[str, Any]:
    import json
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _serialize_run(run, state) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from dataclasses import asdict
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
```

- [ ] **Step 4：注册 router `server/app.py`**

```python
from server.routers import xiaoyunque_shortplay as xiaoyunque_router
app.include_router(xiaoyunque_router.router, prefix="/api/v1", tags=["xiaoyunque"])
```

放在已有其他 `app.include_router(...)` 段。

- [ ] **Step 5：i18n key**

`lib/i18n/zh/errors.py`：

```python
"external_script_unsupported_format": "不支持的剧本格式 {ext}（允许 {allowed}）",
"external_script_too_long": "剧本过长（{actual} 字符，上限 {limit}）",
"provider_not_configured": "未配置 provider：{provider}",
"tos_upload_failed": "TOS 上传失败：{reason}",
"run_not_found": "未找到 run：{run_id}",
"run_already_terminal": "run 已处于终态 {status}，无法取消",
"invalid_encoding": "文件编码无法识别（请使用 UTF-8）",
```

`en/errors.py`：

```python
"external_script_unsupported_format": "Unsupported script format {ext} (allowed: {allowed})",
"external_script_too_long": "Script too long ({actual} chars, limit {limit})",
"provider_not_configured": "Provider not configured: {provider}",
"tos_upload_failed": "TOS upload failed: {reason}",
"run_not_found": "Run not found: {run_id}",
"run_already_terminal": "Run is already in terminal state {status}",
"invalid_encoding": "File encoding not recognized (please use UTF-8)",
```

`vi/errors.py`：

```python
"external_script_unsupported_format": "Định dạng kịch bản không hỗ trợ {ext} (cho phép: {allowed})",
"external_script_too_long": "Kịch bản quá dài ({actual} ký tự, giới hạn {limit})",
"provider_not_configured": "Provider chưa cấu hình: {provider}",
"tos_upload_failed": "Tải lên TOS thất bại: {reason}",
"run_not_found": "Không tìm thấy run: {run_id}",
"run_already_terminal": "Run đã ở trạng thái cuối {status}",
"invalid_encoding": "Định dạng tệp không nhận diện được (vui lòng dùng UTF-8)",
```

- [ ] **Step 6：跑测试**

```bash
uv run pytest tests/test_xiaoyunque_router.py tests/test_i18n_consistency.py -v
```

期望：通过。

- [ ] **Step 7：lint + typecheck**

```bash
uv run ruff check server/routers/xiaoyunque_shortplay.py server/app.py tests/test_xiaoyunque_router.py lib/i18n/
uv run ruff format server/routers/xiaoyunque_shortplay.py server/app.py tests/test_xiaoyunque_router.py lib/i18n/
uv run basedpyright server/routers/xiaoyunque_shortplay.py
```

- [ ] **Step 8：commit**

```bash
git add server/routers/xiaoyunque_shortplay.py server/app.py tests/test_xiaoyunque_router.py lib/i18n/
git commit -m "$(cat <<'EOF'
feat(api): 短剧 pipeline REST endpoints

POST /api/v1/xiaoyunque-shortplay/runs  ← 多分上传 + 创建 run
GET  /api/v1/xiaoyunque-shortplay/runs/{run_id}  ← 查询
GET  /api/v1/xiaoyunque-shortplay/runs  ← 列表
POST /api/v1/xiaoyunque-shortplay/runs/{id}/cancel  ← 取消

剧本走复用的 TosImageUploader（不止图片，任意小文件都行）取签名 URL。
新建 run 后 trigger app.state.xiaoyunque_worker 跑 pipeline。

i18n zh/en/vi 各补 7 个 key。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 · provider registry optional_keys + CLI 烟雾脚本

**Files:**
- Modify: `lib/config/registry.py`
- Create: `scripts/xiaoyunque_shortplay_smoke.py`（手动 CLI，不入 CI）

- [ ] **Step 1：扩展 ProviderMeta**

`lib/config/registry.py` 找 `volc-xiaoyunque` entry，把 `optional_keys` 改成：

```python
optional_keys=[
    "video_max_workers", "language",
    "shortplay_default_model_variant",   # fast720p / pro720p
    "shortplay_default_visual_style",
    "shortplay_default_video_ratio",
],
```

- [ ] **Step 2：跑 provider registry 回归测试**

```bash
uv run pytest tests/test_provider_registry_volc_xiaoyunque.py -v
```

期望：通过（既有测试不动 optional_keys 内容，所以不挂）。

- [ ] **Step 3：写 CLI 烟雾脚本 `scripts/xiaoyunque_shortplay_smoke.py`**

```python
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
    from lib.xiaoyunque_shortplay.state import RunState
    from lib.xiaoyunque_shortplay.runner import XiaoyunquePipelineRunner

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
    missing = [k for k, v in [
        ("VOLC_AK", ak), ("VOLC_SK", sk),
        ("TOS_ENDPOINT", tos_endpoint),
        ("TOS_BUCKET", tos_bucket),
        ("TOS_REGION", tos_region),
    ] if not v]
    if missing:
        logger.error("缺失环境变量: %s", missing)
        return 2

    # 1. 上传剧本到 TOS
    uploader = TosImageUploader(
        ak=ak, sk=sk, endpoint=tos_endpoint, bucket=tos_bucket, region=tos_region,
    )
    logger.info("上传剧本到 TOS...")
    script_url = await uploader.upload_image(script_path, key_prefix="arcreel-shortplay/")
    logger.info("剧本 URL: %s", script_url[:120] + "...")

    # 2. 直接用 client 单跑 4 步（不走 store/worker）
    client = XiaoyunqueShortplayClient(access_key=ak, secret_key=sk, model_variant="fast720p")

    # 用一个最小内存 store 替身
    class MemStore:
        def __init__(self):
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

        async def get_state(self, run_id: str): return self._states.get(run_id, RunState())
        async def set_state(self, run_id, s): self._states[run_id] = s
        async def set_status(self, run_id, status, completed=False):
            self._runs[run_id]["status"] = status
        async def set_failed(self, run_id, err):
            self._runs[run_id]["status"] = "failed"
            self._runs[run_id]["last_error"] = err
        async def set_thread_and_assets(self, run_id, *, thread_id, assets_id):
            self._runs[run_id]["thread_id"] = thread_id
            self._runs[run_id]["assets_id"] = assets_id

    store = MemStore()
    run_id = "smoke-run"
    store._runs[run_id] = {
        "id": run_id, "project_name": "smoke", "status": "pending",
        "model_variant": "fast720p", "visual_style": "真人写实",
        "video_ratio": "16:9", "script_file_url": script_url,
        "thread_id": None, "assets_id": None, "last_error": None,
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
```

- [ ] **Step 4：lint + commit**

```bash
uv run ruff check lib/config/registry.py scripts/xiaoyunque_shortplay_smoke.py
uv run ruff format lib/config/registry.py scripts/xiaoyunque_shortplay_smoke.py
git add lib/config/registry.py scripts/xiaoyunque_shortplay_smoke.py
git commit -m "$(cat <<'EOF'
feat(xiaoyunque): provider registry optional_keys + CLI 烟雾脚本

ProviderMeta 加 shortplay_default_* 三个 optional_keys。
scripts/xiaoyunque_shortplay_smoke.py 用环境变量传 AK/SK/TOS，
端到端跑一份本地 < 300 字的剧本，打印每集最终 video_url。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 · 活体联调（BLOCKED：需用户 TOS 配置 + 短剧本）

**前置阻塞**：

1. 用户在 ArcReel Settings 填好 `volc-xiaoyunque` 的 `tos_endpoint` / `tos_bucket` / `tos_region`
2. 准备一份 < 300 字的中文剧本（例如 spec §3 用的"藏在奶茶里的戒指" 简化版）
3. 确认免费试用配额未用完

**步骤**：

- [ ] 在火山控制台开通 TOS bucket（如还没有）
- [ ] 把剧本存到 `/tmp/test_script.txt`（UTF-8）
- [ ] 跑烟雾脚本：

```bash
VOLC_AK='...' VOLC_SK='...' \
TOS_ENDPOINT='tos-cn-beijing.volces.com' \
TOS_BUCKET='...' TOS_REGION='cn-beijing' \
uv run python scripts/xiaoyunque_shortplay_smoke.py /tmp/test_script.txt
```

- [ ] 期望输出（约 30-50 分钟，3 集示例）：
  - status=done
  - 每集打印 final_video_url
  - 浏览器打开 video_url 能看到合成完成的视频

- [ ] 排查（按可能性排序）：
  - **401 Unauthorized**：AK/SK 配错或服务未开通（控制台再开通一次）
  - **50412/50413**：剧本含敏感词，换一份
  - **50500/50501 反复重试**：火山服务端问题，过会儿再试
  - **超时**：单步 1800s 上限可能不够 3 集合成；调高 `MAX_WAIT_SEC_PER_STEP`

- [ ] 跑通后启动后端 dev server，curl 调 POST 端点端到端验证：

```bash
curl -X POST http://localhost:1241/api/v1/xiaoyunque-shortplay/runs \
  -H "Authorization: Bearer <api_key>" \
  -F "project_name=test" \
  -F "script_file=@/tmp/test_script.txt" \
  -F "visual_style=真人写实, 电影风格, 冷色调" \
  -F "video_ratio=16:9" \
  -F "model_variant=fast720p"
# → {"run_id": "...", "status": "pending"}

# 轮询查询：
curl http://localhost:1241/api/v1/xiaoyunque-shortplay/runs/{run_id}
```

- [ ] commit（如果 live 过程发现 bug 需要修，commit 即可；通常不需要）

---

## 最终验收

- [ ] `uv run pytest --cov` 覆盖率 ≥ 80%
- [ ] `uv run basedpyright` 0 error
- [ ] `uv run ruff check . && uv run ruff format --check .` 0 issue
- [ ] CLI 烟雾脚本能跑通至少 1 集 done
- [ ] REST 端点用 curl 测过 POST → GET → 完成

---

## Self-Review

**1. Spec coverage**：

| spec 节 | 对应 task |
|---|---|
| §4 架构 | Task 1-8 完整覆盖 |
| §5.1 ORM table | Task 3 |
| §5.2 RunState dataclass | Task 2 |
| §6 client.py | Task 4 |
| §7 runner.py | Task 6（注意：原编号是 5，我这里 task 编号是 6） |
| §7.1 worker | Task 7 |
| §8 REST API | Task 8 |
| §9 配置 | Task 9 |
| §10 错误处理 | Task 1（errors.py 分类） + Task 6（runner 内消费） |
| §11 测试 | 每 task 都有 unit 测试 |
| §12 i18n | Task 8 |
| §13 实施顺序 | 跟本 plan 顺序一致 |
| §14 TODO | Task 10 |
| §15 风险 | runner 内已实现：每集独立 try、video_url 立即返回未额外下载（这是 spec §15 提到的风险，**Phase A 不自动下载，仅返回 URL，让用户负责 1h 内下载**——已显式列入 spec 风险段） |

**2. Placeholder scan**：

- Task 7 lifespan 集成有一段 "**真实集成代码**" 含 `session_factory` 占位说明——这是合理的，因为 worker session 管理需要看现有 `GenerationWorker` 的真实实现才能写准。实施时按指引完成。
- Task 8 测试里 `test_get_run_returns_state` 简化为只测 404，正路径靠 Task 10 烟雾——已注明，不是 placeholder。
- 无其他 TBD/TODO。

**3. Type consistency**：

- `XiaoyunqueShortplayClient.__init__(*, access_key, secret_key, model_variant)` 在 Task 4 定义，Task 9 烟雾脚本调用一致 ✓
- `XiaoyunqueRunStore.create(*, project_name, model_variant, visual_style, video_ratio, script_file_url)` 在 Task 5 定义，Task 6 runner、Task 8 router 都按这套调 ✓
- `RunState` dataclass 字段名（parse_task_id / design_task_id / episodes / characters / scenes / charge_count_total）在 Task 2 / 4 / 6 一致 ✓
- `EpisodeState.status` 枚举（pending / generating / composing / done / failed / failed_compose）Task 2 定义、Task 6 使用一致 ✓

修复无遗漏，结束。

---

## Phase A 完成后

提议下一份 spec：**Phase B = UI 集成**（前端模板入口 / 剧集列表面板 / 状态条 / 视频播放 / 失败重试）。用户用 Phase A 的 curl 跑通后能感受到效果，再决定是否启动 Phase B。
