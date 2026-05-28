# 小云雀短剧 Agent pipeline 接入（Phase A：后端 + API）设计

**日期**：2026-05-28
**关联组件**：`lib/video_backends/` / `lib/xiaoyunque_shortplay/`（新模块）/ `server/routers/`
**前置依赖**：`feat/shot-prompt-copy-video-import-xiaoyunque` 分支（PR #2）已合并 main 或基于其之上

## 1. 背景与动机

火山小云雀提供两套独立产品：

| 产品 | 接入方式 | 当前状态 |
|---|---|---|
| 智能生视频 Agent 2.0 | 单步 API：prompt + 可选参考图 → 一个 mp4 | PR #2 已接入活体验证 |
| **短剧漫剧 Agent** | **4 步 pipeline**：剧本解析 → 图片生成 → 视频生成 → 视频合成 | **本 spec 接入** |

用户已开通**两套**短剧 Agent 的免费试用：
- 剧本解析 / 图片生成 / 视频生成 Seedance 2.0 fast 720p / 视频生成 Seedance 2.0 720p / 视频合成 fast / 视频合成 pro

短剧 Agent 内部步骤强耦合——`assets_id` 和 `thread_id` 由剧本解析创建、贯穿后续 3 步必填。**不能拆开"用 GPT 解析 + 用小云雀生图"**，要用必须整套用。

## 2. 范围

**Phase A（本 spec）内**：

- 新模块 `lib/xiaoyunque_shortplay/`：4 步 API 封装 + 项目级 runner
- 剧本文件上传到 TOS（接口要求 `file_url` 公网可达）
- 项目级状态机：`pending → parsing → designing → generating → composing → done`
- 每剧集独立追踪：`pending → generating → composing → done`
- REST API：触发 / 查询 / 取消 / 列举
- 默认走 **Seedance 2.0 fast 720p**（用户已开通免费试用，更省 quota）
- 测试：mock 4 步 API 端对端
- CLI 烟雾脚本可手工真跑

**Phase A 不做（→ Phase B）**：

- 任何 React 前端 UI：剧集卡片、状态条、播放器
- 跟现有 `narration / drama` content_mode 的 UI 复用
- 重生成单个失败剧集 / 单个失败分镜的细粒度操作
- Seedance 2.0 pro 720p / fast↔pro 切换
- 配额预估 + 实时余额查询
- 跨 worker 实例的协作分布式编排

**永远不做**：

- 短剧 Agent **替换** 现有 narration / drama 模式：是平行第三种模式，互不影响
- 用 GPT 做剧本解析后再喂给小云雀图片生成：接口不支持

## 3. 用户故事（Phase A 完成后）

> 用户用 curl 或 httpie 调一个 POST 端点上传剧本：

```bash
curl -X POST http://localhost:1241/api/v1/xiaoyunque-shortplay/runs \
  -H "Authorization: Bearer xxx" \
  -F "project_name=test_xiaoyunque" \
  -F "script_file=@./藏在奶茶里的戒指.docx" \
  -F "visual_style=真人写实, 电影风格, 冷色调" \
  -F "video_ratio=16:9" \
  -F "model_variant=fast720p"
```

> 后端立即返回 `{run_id, project_name}`，pipeline 在后台跑。

> 用户轮询查询：

```bash
curl http://localhost:1241/api/v1/xiaoyunque-shortplay/runs/{run_id}
```

返回完整状态：

```json
{
  "run_id": "...",
  "project_name": "test_xiaoyunque",
  "status": "generating",
  "thread_id": "ark_3572523320385040940",
  "assets_id": "ark_1194097890828",
  "script": { "title": "藏在奶茶里的戒指", "episode_count": 3 },
  "characters": [...],
  "scenes": [...],
  "episodes": [
    { "episode_id": "1", "title": "奶茶店里的求婚秘密", "status": "done",
      "video_url": "https://..../ep1.mp4", "cover_url": "https://...",
      "shots": [{"shot_id": "S1", "status": 3, "video_url": "..."}, ...] },
    { "episode_id": "2", "status": "generating", "shots": [...] },
    { "episode_id": "3", "status": "pending" }
  ],
  "started_at": "2026-05-28T...",
  "updated_at": "...",
  "errors": []
}
```

> 任意一集 done 时，video_url 立即可下载（TTL 1h，需要尽快拉到本地）。

## 4. 架构总览

```
┌── server/routers/xiaoyunque_shortplay.py（新）──────────────┐
│  POST /api/v1/xiaoyunque-shortplay/runs              ← 启动 │
│  GET  /api/v1/xiaoyunque-shortplay/runs/{run_id}     ← 查询 │
│  GET  /api/v1/xiaoyunque-shortplay/runs              ← 列表 │
│  POST /api/v1/xiaoyunque-shortplay/runs/{id}/cancel  ← 取消 │
└─────────────────────────────────────────────────────────────┘
                              │
┌── lib/xiaoyunque_shortplay/（新模块）─────────────────────────┐
│  client.py        ← 4 步 API 封装（薄壳调 VisualService）    │
│  runner.py        ← XiaoyunquePipelineRunner 编排            │
│  state.py         ← RunState dataclass + 状态机 transitions  │
│  store.py         ← SQLAlchemy 持久化（lib/db/ 新增 table）  │
│  errors.py        ← XiaoyunqueError 家族（按错误码分类）     │
│  models.py        ← Pydantic：req/resp_data 反序列化         │
└─────────────────────────────────────────────────────────────┘
                              │
┌── lib/db/models/xiaoyunque_run.py（新 ORM table）───────────┐
│  XiaoyunqueRun(id, project_name, status, thread_id,         │
│                assets_id, payload_json, created_at, ...)    │
└─────────────────────────────────────────────────────────────┘
                              │
┌── lib/volc_tos_uploader.py（已存在，复用上传剧本）──────────┐
│  上传 .docx / .txt → 取签名 URL → 喂给剧本解析 file_url      │
└─────────────────────────────────────────────────────────────┘
```

**关键边界**：

- **不复用** `lib/generation_queue.py` / `GenerationWorker`——那套是 shot/scene 单元，短剧 pipeline 是项目级长任务
- **不复用** 现有 `MediaGenerator` / `GenerationTask`——pipeline 不产 "scene_001.mp4"，产 "episode_1.mp4"
- **不复用** `lib/video_backends/`——短剧 Agent 不返回 `VideoGenerationResult` 单元素，返回整集；语义不同

新模块完全平行存在，**零侵入**现有 ArcReel 生成体系。

## 5. 数据模型

### 5.1 ORM table `xiaoyunque_runs`

新 SQLAlchemy ORM 模型（`lib/db/models/xiaoyunque_run.py`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | run_id |
| `project_name` | str | 关联 ArcReel 项目（仅作 namespace；不必是现有 narration/drama 项目） |
| `status` | enum | `pending / parsing / designing / generating / composing / done / failed / cancelled` |
| `model_variant` | str | `fast720p` / `pro720p` |
| `visual_style` | str | 用户输入的视觉风格 |
| `video_ratio` | str | "16:9" / "9:16" 等 |
| `thread_id` | str \| null | 剧本解析返回；后续步骤必传 |
| `assets_id` | str \| null | 同上 |
| `script_file_url` | str | TOS 签名 URL（剧本上传后） |
| `state_json` | JSONB | 复杂状态：episodes 列表、shots 状态、最终视频 URL、错误等 |
| `created_at / updated_at / completed_at` | timestamp | |
| `last_error` | text \| null | 失败时记录 |

**alembic 迁移**：`alembic/versions/2026_05_28_add_xiaoyunque_runs.py`

### 5.2 `state_json` 内部结构（dataclass `RunState`）

```python
@dataclass
class EpisodeState:
    episode_id: str
    episode_asset_id: str
    title: str
    status: Literal["pending", "generating", "composing", "done", "failed"]
    shots: list[ShotState]                       # 来自视频生成步的 storyboard_detail.Shots
    video_generate_task_id: str | None = None
    video_compose_task_id: str | None = None
    final_video_url: str | None = None           # 来自合成步
    final_cover_url: str | None = None
    error_message: str | None = None

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
    image_urls: list[str]                        # 各 appearance 的 ImageURL

@dataclass
class RunState:
    parse_task_id: str | None = None             # 剧本解析的 task_id
    design_task_id: str | None = None            # 图片生成的 task_id
    script: ScriptSummary | None = None          # CoreElement + EpisodeCount 等
    characters: list[CharacterState] = ...
    scenes: list[SceneState] = ...
    episodes: list[EpisodeState] = ...
    charge_count_total: int = 0                  # 累计 charge_count
```

`state_json` 列存储这个 dataclass 的 dict 化结果，每步推进时整体覆盖写。

## 6. 4 步 API 封装（`client.py`）

复用 `VolcXiaoyunqueBackend` 已经验证过的 `VisualService` 调用模式。新建 `XiaoyunqueShortplayClient` 包装：

```python
class XiaoyunqueShortplayClient:
    """4 步 API 的同步 + 异步封装（内部走 VisualService SDK）。"""

    def __init__(self, *, access_key: str, secret_key: str, model_variant: str = "fast720p"):
        self._visual = VisualService()
        self._visual.set_ak(access_key)
        self._visual.set_sk(secret_key)
        self._model_variant = model_variant  # "fast720p" / "pro720p"

    # --- Step 1: 剧本解析 ---
    async def submit_script_analysis(self, *, visual_style: str, video_ratio: str,
                                     file_url: str, file_type: str, file_name: str) -> str:
        """返回 task_id"""

    async def query_script_analysis(self, task_id: str) -> ScriptAnalysisResult:
        """返回结构化的 thread_id / assets_id / script_detail"""

    # --- Step 2: 图片生成 ---
    async def submit_material_design(self, *, assets_id: str, thread_id: str, run_id: str) -> str: ...
    async def query_material_design(self, task_id: str) -> MaterialDesignResult: ...

    # --- Step 3: 单集视频生成 ---
    async def submit_video_generate(self, *, assets_id: str, thread_id: str,
                                    episode_id: str, run_id: str) -> str: ...
    async def query_video_generate(self, task_id: str) -> VideoGenerateResult: ...

    # --- Step 4: 单集视频合成 ---
    async def submit_video_compose(self, *, assets_id: str, thread_id: str, episode_id: str) -> str: ...
    async def query_video_compose(self, task_id: str) -> VideoComposeResult: ...
```

`req_key` 内部根据 `model_variant` 自动选：

| 步骤 | fast720p req_key | pro720p req_key |
|---|---|---|
| 剧本解析 | `pippit_shortplay_cvtob_script_analysis` | 同左 |
| 图片生成 | `pippit_shortplay_cvtob_material_design` | 同左 |
| 视频生成 | `pippit_shortplay_cvtob_video_generate_fast720p` | `pippit_shortplay_cvtob_video_generate_pro720p` |
| 视频合成 | `pippit_shortplay_cvtob_video_compose_fast720p` | `pippit_shortplay_cvtob_video_compose_pro720p` |

通用错误码处理沿用 PR #2 已经实现的 `_RETRYABLE_BUSINESS_CODES` 集合。

## 7. Runner（`runner.py`）

```python
class XiaoyunquePipelineRunner:
    """编排 4 步 pipeline。设计为可恢复：每步成功后立刻写库，断点续传。"""

    def __init__(self, client: XiaoyunqueShortplayClient, store: XiaoyunqueRunStore):
        self.client = client
        self.store = store

    async def run(self, run_id: UUID) -> None:
        """从 store 读取当前 status，从下一未完成步开始执行。"""
        run = await self.store.get(run_id)

        if run.status in ("done", "failed", "cancelled"):
            return  # 终态不重跑

        try:
            if run.status == "pending":
                await self._step1_parse(run)
            if run.status == "parsing":
                await self._wait_parse_done(run)
            if run.status == "designing":
                await self._wait_design_done(run)
            if run.status == "generating":
                await self._step3_for_each_episode(run)
            if run.status == "composing":
                await self._step4_for_each_episode(run)
            await self.store.set_status(run.id, "done", completed_at=now())
        except XiaoyunqueError as e:
            await self.store.set_failed(run.id, str(e))
            raise
```

要点：

- **每步完成立即写库**，下次进程重启可从 `status` 恢复
- 视频生成 / 视频合成是**每集独立**——3 集就是 3 + 3 = 6 个子任务，串行跑（避免免费试用 QPS=1 触雷）
- 中间任意一集失败：跳过、继续下一集，最后 status="done" 但 episode.status="failed"
- 全部失败：status="failed"

### 7.1 后台执行入口

新增 lifespan-hooked `XiaoyunqueWorker`，类似现有 `GenerationWorker` 但简化为：

- 启动时 `SELECT * FROM xiaoyunque_runs WHERE status NOT IN ('done', 'failed', 'cancelled')` 拉取在途 run
- 每个 run 起一个 asyncio task 跑 runner
- 进程关停时 cancel pending task（不影响已写库状态）
- 不需要分布式 lease——单进程即可（短剧 pipeline 计算密集都在火山侧）

## 8. REST API（`server/routers/xiaoyunque_shortplay.py`）

### `POST /api/v1/xiaoyunque-shortplay/runs`

请求（multipart）：
- `project_name` (str, 必填，作 namespace)
- `script_file` (UploadFile, 必填，.docx / .txt / .pdf)
- `visual_style` (str, 必填)
- `video_ratio` (str, 默认 "16:9")
- `model_variant` (str, 默认 "fast720p"，可选 "pro720p")

行为：
1. 校验扩展名 + 字数（≤ 300 字符——文档限制；超了 400）
2. 上传到 TOS 取签名 URL
3. 在 `xiaoyunque_runs` 插入新行，status=`pending`
4. **后台异步** 触发 runner（不阻塞 HTTP）
5. 返回 `{run_id, status}`

### `GET /api/v1/xiaoyunque-shortplay/runs/{run_id}`

返回完整 RunState dict（见 §3 示例）。

### `GET /api/v1/xiaoyunque-shortplay/runs?project_name=xxx&status=done`

列举，支持 project 与 status 过滤。

### `POST /api/v1/xiaoyunque-shortplay/runs/{run_id}/cancel`

把 status 置 `cancelled`，正在跑的 runner 下次步进时检测到立即返回。

## 9. 配置

`lib/config/registry.py` 的 `volc-xiaoyunque` ProviderMeta 加 optional keys：

```python
optional_keys=[
    "video_max_workers", "language",
    "shortplay_default_model_variant",   # "fast720p" | "pro720p"
    "shortplay_default_visual_style",
    "shortplay_default_video_ratio",
],
```

剧本解析需要的 TOS 上传**复用** PR #2 已经接入的 `tos_endpoint / tos_bucket / tos_region`。

## 10. 错误处理

| 场景 | 行为 |
|---|---|
| 剧本超 300 字 | 400 `external_script_too_long` |
| TOS 上传失败 | runner.status=failed，last_error 记录 |
| 剧本解析 50412/50413（文本审核） | status=failed，last_error 含原因 |
| 图片生成部分失败（character/scene 渲染失败） | 在 state 标 partial，但 status 继续推进（后续视频生成会跳过这些角色） |
| 视频生成单集失败 | episode.status=failed，跳过该集合成，继续下一集 |
| 视频合成失败 | episode.status=failed_compose（视频已生成可下载） |
| 任意步 status=expired/not_found | episode 标 expired，**不自动重试**（重新提交整集会重新计费） |

## 11. 测试

- `tests/test_xiaoyunque_shortplay_client.py` — mock VisualService，验证 req_key 选 fast/pro、字段映射
- `tests/test_xiaoyunque_runner.py` — mock client，跑完整 happy path + 单集失败 + cancel
- `tests/test_xiaoyunque_routers.py` — TestClient，验证 multipart 上传、查询、列表、取消
- `tests/test_xiaoyunque_store.py` — async_session fixture，验证 status transitions
- 现有 `test_i18n_consistency.py` 自动覆盖新增 i18n key

## 12. i18n

新增 i18n key（zh/en/vi 三语）：

errors：`external_script_too_long` / `script_analysis_failed` / `material_design_failed` / `video_generate_failed` / `video_compose_failed` / `run_not_found` / `run_already_cancelled`

system：`xiaoyunque_run_started` / `xiaoyunque_run_completed`

providers：`shortplay_default_model_variant` 等 optional_key 名称的 label / description

## 13. 实施顺序

按 plan 拆分（writing-plans 阶段细化）：

1. ORM table + alembic migration
2. `models.py` Pydantic + dataclass
3. `client.py` 4 步 API 封装 + unit test（mock VisualService）
4. `store.py` 持久化 + unit test（async_session）
5. `runner.py` 编排 + unit test（mock client）
6. `XiaoyunqueWorker` 后台启动 / 关停
7. REST API router
8. i18n key 三语
9. CLI 手工烟雾脚本：上传一份 < 300 字示例剧本，跑完整 4 步并打印每步状态

## 14. 已知 TODO（实施前需用户确认）

1. **剧本字数限制**：文档说 300 字，但具体是字符数还是 token？需用真实剧本验证。
2. **fast720p 默认值是否合适**：用户已开通免费试用，pro 也开通了——MVP 用 fast 还是 pro？
3. **TOS bucket 是否已经备好**：用户 PR #2 还没填 TOS 配置；本 spec 实施前必须 ready，否则剧本上传无法工作。

## 15. 风险与回退

- **风险**：免费试用配额 QPS=1，多个 run 并发会 429；缓解：runner 内部 step 串行，多 run 之间也建议串行（XiaoyunqueWorker 配 1 个 lane）
- **风险**：video_url TTL 1 小时，runner 跑慢可能拿不到；缓解：每集 done 立即下载到本地 `projects/{project_name}/xiaoyunque_runs/{run_id}/episode_{n}.mp4`
- **风险**：state_json 列写过大（angularly nested），缓解：用 JSONB（PG）或 TEXT（SQLite），单行不超 1 MiB 就 OK
- **回退**：本 spec 完全平行于现有体系。任一文件 revert 即可下线；ORM table 留下不影响其他功能。

## 16. Phase B 预告（不在本 spec 内）

- 前端新建项目模板加 "小云雀短剧 Agent" 选项
- 剧集列表面板：每集状态条 + 完成视频播放 + 失败重试
- 配额预估 / 余额查询入口
- Seedance pro/fast 切换 UI
- 把 final_video_url 下载后纳入项目资产库（与 Phase A 的 "本地落盘" 替换）
