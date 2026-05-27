# 分镜 prompt 复制 + 外部视频导入 + 小云雀视频后端 设计

**日期**：2026-05-28
**关联组件**：`MediaCard` / `generation_tasks` / `video_backends` / `files` / `config/registry`

## 1. 背景与动机

ArcReel 的视频生成走项目配置的视频 API（Ark / Veo / Sora / Vidu 等）。在用户配额不足、想试别家模型、或本地后端临时不可用时，目前没有"逃生通道"——既不能拿到当前镜头的完整 prompt 去外部平台手动生成，也不能把外部生成好的视频塞回到原镜头位置。

同时，火山引擎刚发布的 **小云雀-智能生视频 Agent 2.0** 是一款支持「有参考图 + 文本」与「纯文本」两种模式的视频模型，需要作为新视频后端接入。

这一份 spec 把三件事打包：

1. **Feature A**：在 `MediaCard` 的"生成视频"按钮旁加 **"复制 prompt"** 入口，弹出面板展示最终 prompt 与全部参考图，每张图都能单独复制或下载，并提供 ZIP 打包下载
2. **Feature B**：在同一位置加 **"导入视频"** 入口，接收本地 `.mp4 / .mov / .webm` 文件，作为一个新版本写入 `VersionManager`
3. **Feature C**：接入 **火山小云雀** 视频后端（有参考 + 无参考两个接口），新写一份 Volcengine 视觉服务签名工具 + TOS 上传辅助

三件事在用户体验上自成闭环：小云雀给本地直跑路径；复制 prompt + 导入视频给"任何外部模型"的逃生通道。

## 2. 范围与非目标

**范围内**

- MediaCard 改造：新增 2 个图标按钮（复制 prompt、导入视频）+ 2 个 Modal
- 后端新端点：`GET /api/v1/projects/{name}/episodes/{episode}/shots/{segment_id}/video-prompt-bundle` 与 `.zip` 变体；`POST .../import-video`
- 重构：把 `server/services/generation_tasks.py::_normalize_video_prompt` + `_collect_reference_images` 抽到独立模块 `server/services/video_prompt_resolver.py`，让 worker 和新端点共用
- 新模块：`lib/volc_visual_shared.py`（签名 v4）、`lib/volc_tos_uploader.py`（TOS 上传）、`lib/video_backends/volc_xiaoyunque.py`
- provider 注册：`PROVIDER_VOLC_XIAOYUNQUE` 加入 `lib/providers.py` / `lib/config/registry.py` / `lib/video_backends/__init__.py` / `lib/generation_worker.py` 池配置
- i18n：zh/en/vi 同步新增对应 key
- 测试：单元 + 关键端点的集成测试，覆盖率不低于 80%

**明确非目标**

- **不做** "导入外部视频 URL"（仅本地文件上传）；不实现把已生成视频再上传到外部分发的镜像
- **不做** 批量"复制全部镜头 prompt"（仅单镜头）
- **不做** 小云雀的细粒度费用统计（首版只记录时长，cost_calculator 留 TODO 单价位）
- **不做** 改动 `_global_assets` 上传逻辑
- **不动** Storyboard 卡（仅在视频 MediaCard 上加按钮）
- 小云雀**「无参考」接口的 `req_key`** 和**轮询 Action 名**：用户提供的文档只给了「有参考」一份；spec 内放 TODO 占位，实施前需补 doc 或本地实验确认

## 3. 用户故事

1. **A**："这个分镜的视频生成额度不够了。我想拿同样的 prompt + 参考图去 [外部平台 X] 试试。"
   → 用户点击 MediaCard 上"复制 prompt"按钮 → 弹出面板 → 看到 final prompt（一键复制）+ 一组参考图（每张图独立复制/下载，或全部 ZIP 下载）→ 切到外部平台粘贴 + 上传

2. **B**："我在外部平台生成好了视频，下载到本地了。我想把它放回 ArcReel 的对应镜头位置。"
   → 用户点击 MediaCard 上"导入视频"按钮 → 选本地 mp4 → 上传成功 → MediaCard 立即播放新视频；版本时间机里多出一个 `external` 来源的版本

3. **C**："我想用火山小云雀生成这个镜头的视频。"
   → 用户在系统设置里勾选 `volc-xiaoyunque` 作为视频 provider 并配置 AK/SK + TOS bucket → 点"生成视频" → worker 走小云雀后端，自动把参考图上传 TOS 取签名 URL → 提交任务、轮询、下载结果

## 4. 架构总览

```
┌── Frontend (frontend/src/) ────────────────────────────────────┐
│  MediaCard.tsx (+ 2 icon buttons)                              │
│    ├── VideoPromptCopyModal.tsx  ── GET .../video-prompt-bundle│
│    │     └── 一键 ZIP 下载  ─── GET .../video-prompt-bundle.zip│
│    └── VideoImportModal.tsx      ── POST .../import-video      │
└────────────────────────────────────────────────────────────────┘
                              │
┌── Backend (server/) ───────────────────────────────────────────┐
│  routers/shots.py  (新)                                        │
│    ├── GET .../video-prompt-bundle  (json)                     │
│    ├── GET .../video-prompt-bundle.zip                         │
│    └── POST .../import-video                                   │
│                              │                                 │
│  services/video_prompt_resolver.py (新, 重构抽出)              │
│    └── resolve_video_prompt_bundle(project, episode, seg) →    │
│        VideoPromptBundle { prompt: str, refs: [Ref] }          │
│                                                                │
│  services/generation_tasks.py (改: 调用上面 resolver)          │
└────────────────────────────────────────────────────────────────┘
                              │
┌── Lib (lib/) ──────────────────────────────────────────────────┐
│  video_backends/volc_xiaoyunque.py (新)                        │
│    ├── 提交: visual.volcengineapi.com?Action=CVSync2AsyncSubmit│
│    └── 轮询: visual.volcengineapi.com?Action=CVSync2AsyncGet   │
│                              │                                 │
│  volc_tos_uploader.py (新) ── 把本地图 PUT 到 TOS 取预签 URL   │
│  volc_visual_shared.py (新) ── Volcengine Sig v4 签名          │
│                                                                │
│  providers.py:    PROVIDER_VOLC_XIAOYUNQUE                     │
│  config/registry.py: ProviderMeta + 一个 xiaoyunque-2.0 模型   │
│  video_backends/__init__.py: register_backend(...)             │
│  generation_worker.py: 池配置 (5, 3)                           │
└────────────────────────────────────────────────────────────────┘
```

## 5. Feature A：复制 prompt + 参考图

### 5.1 后端

#### 新模块 `server/services/video_prompt_resolver.py`

**目的**：将 `generation_tasks.py` 里现存的 `_normalize_video_prompt`（行 446-486）与 `_collect_reference_images`（行 550-576）抽出来，做成可复用的纯函数 + dataclass 返回值。

```python
@dataclass
class VideoReferenceImage:
    kind: Literal["character_sheet", "scene_sheet", "prop_sheet",
                  "start_image", "end_image", "previous_storyboard", "extra"]
    label: str           # 人物名 / 场景名 / 道具名 / "首帧" / ...
    relative_path: str   # project_dir 相对路径
    url: str             # /api/v1/files/{project}/{relative_path}
    filename: str

@dataclass
class VideoPromptBundle:
    shot_id: str
    prompt: str                                # 已 normalize + 反向提示词追加
    reference_images: list[VideoReferenceImage]
    duration_seconds: int
    aspect_ratio: str

async def resolve_video_prompt_bundle(
    project_name: str,
    episode: int,
    segment_id: str,
) -> VideoPromptBundle: ...
```

实现要点：

- **复用现有 prompt 装配链**：调用 `_normalize_video_prompt` 与 `lib/prompt_builders.append_video_negative_tail`，确保跟 worker 真正发给后端的 prompt **字符一致**
- **复用 `_collect_reference_images`**：从 segment 的 `characters_in_segment / scenes / props` 收集 sheet 路径，并补上首帧（已生成的 storyboard image）与可能的尾帧
- **URL 生成**：直接拼 `/api/v1/files/{project}/{relative_path}`；不带签名（前端用 `<img src>` 加载，走现有静态资源端点 + auth cookie / token）

`generation_tasks.py` 后续仅保留薄壳调用 resolver，把 prompt 装配逻辑下沉。

#### 新路由 `server/routers/shots.py`

挂载在 `/api/v1/projects/{project_name}/episodes/{episode}/shots/{segment_id}/`：

**`GET /video-prompt-bundle`** → JSON

```json
{
  "shot_id": "001",
  "prompt": "action: ...\ncamera_motion: ...\n...负面提示词追加文本",
  "reference_images": [
    {"kind": "character_sheet", "label": "李雷", "url": "/api/v1/files/...", "filename": "lilei.png"},
    {"kind": "scene_sheet",    "label": "教室", "url": "/api/v1/files/...", "filename": "classroom.png"},
    {"kind": "start_image",    "label": "首帧 v3", "url": "/api/v1/files/...", "filename": "scene_001_v3.png"}
  ],
  "duration_seconds": 5,
  "aspect_ratio": "9:16"
}
```

**`GET /video-prompt-bundle.zip`** → `application/zip` 流式

ZIP 内容：

```
prompt.txt              ← UTF-8，结尾换行
references/
  01_character_lilei.png
  02_scene_classroom.png
  03_start_frame.png
README.txt              ← 简要说明字段含义 + 生成时间
```

文件名前缀 `NN_` 保证排序稳定；缺省用 `shot_{segment_id}_video_bundle.zip` 作下载文件名（`Content-Disposition: attachment`）。

服务端用 `zipfile.ZipFile` 写入临时 buffer（`io.BytesIO`），不写磁盘；走 `StreamingResponse` 返回。

### 5.2 前端

#### `MediaCard.tsx` 改动

只对 `kind === "video"` 渲染两个新 icon 按钮，放在 **Generate CTA 上方右侧**（即 header 行 `VersionTimeMachine` 旁边，复用 `flex` 容器）：

```
┌──────────────────────────────────────────────────┐
│ 🎬 视频    [↻] [📋复制] [⤴导入]  v3 ▼ │  ← header
├──────────────────────────────────────────────────┤
│              [ 视频播放器 ]                     │
├──────────────────────────────────────────────────┤
│         [✨ 生成视频   ~$0.12 ]                 │
└──────────────────────────────────────────────────┘
```

新增 props：

```ts
onCopyPrompt?: () => void;
onImportVideo?: () => void;
```

按钮：`Clipboard` 图标 + `Upload` 图标（lucide-react），高度跟 `VersionTimeMachine` 对齐；title tooltip 使用 i18n key。

#### `VideoPromptCopyModal.tsx`（新）

```
┌── 视频生成材料 ────────────────────── [×] ──┐
│  Shot 001 · 5s · 9:16                       │
│                                              │
│  最终 Prompt                       [复制全部]│
│  ┌──────────────────────────────────────┐   │
│  │ action: ...                          │   │
│  │ camera_motion: ...                   │   │
│  │ ambiance_audio: ...                  │   │
│  │ dialogue: ...                        │   │
│  │ ...                                  │   │
│  │ 负向提示词: 不要 ...                 │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  参考图（3 张）                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ [缩略]   │ │ [缩略]   │ │ [缩略]   │    │
│  │ 李雷     │ │ 教室     │ │ 首帧 v3  │    │
│  │ 📋  ⬇   │ │ 📋  ⬇   │ │ 📋  ⬇   │    │
│  └──────────┘ └──────────┘ └──────────┘    │
│                                              │
│  [⬇ 打包下载 ZIP（含 prompt + 3 张图）]     │
└──────────────────────────────────────────────┘
```

行为：

- **复制全部**：`navigator.clipboard.writeText(prompt)`，按钮文本切换为"已复制"+ 1.5s 还原
- **每张图 📋（复制）**：
  - 优先用 `navigator.clipboard.write([new ClipboardItem({ [mime]: blob })])`
  - 浏览器/系统不支持 image blob 时（catch 异常），fallback 到复制图片 URL 文本并 toast 提示"已复制图片链接"
- **每张图 ⬇（下载）**：`<a download>` 触发
- **打包下载 ZIP**：`window.location = bundleZipUrl` 或新建 `<a download>` 元素
- 复用 `frontend/src/api.ts` 现有 `API.request` 风格添加 `API.getVideoPromptBundle()` 与 `API.getVideoPromptBundleZipUrl()`

#### `api.ts` 新方法

```ts
static async getVideoPromptBundle(
  projectName: string, episode: number, segmentId: string
): Promise<VideoPromptBundleDTO> { ... }

static getVideoPromptBundleZipUrl(
  projectName: string, episode: number, segmentId: string
): string {
  return `/api/v1/projects/${encodeURIComponent(projectName)}` +
         `/episodes/${episode}/shots/${encodeURIComponent(segmentId)}` +
         `/video-prompt-bundle.zip`;
}
```

类型在 `frontend/src/types/script.ts` 旁新建 `frontend/src/types/video-prompt.ts`。

## 6. Feature B：导入外部视频

### 6.1 后端

#### 新路由 `POST /api/v1/projects/{name}/episodes/{episode}/shots/{segment_id}/import-video`

- 用 FastAPI `UploadFile`；接受单文件
- 允许扩展名：`.mp4 .mov .webm`
- 大小上限：**200 MiB**（与小云雀单视频限制对齐；常量放 `lib/limits.py` 或新建）
- 流程：

  1. 校验扩展名 + 大小（流式读取，超限早返 413）
  2. 写到临时文件
  3. 若是 mp4：直接采用；若是 mov/webm：用 `ffmpeg -i in -c copy out.mp4`（音视频 stream copy，不重编码）做容器转换。失败回退到 `-c:v libx264 -c:a aac` 兜底
  4. 调用现有 ffmpeg 缩略图工具生成 `thumbnails/scene_{segment_id}.jpg`（第 1 秒帧）
  5. 通过 `ProjectManager.update_scene_asset(asset_type="video_clip", asset_path="videos/scene_{id}.mp4")` + 同样的 thumbnail 更新
  6. `VersionManager.add_version(resource_type="videos", resource_id=segment_id, metadata={"source": "external", "original_filename": "...", "uploaded_at": "..."})`
  7. emit project event（同 `generation_tasks` 视频完成路径）

返回：

```json
{
  "success": true,
  "video_path": "videos/scene_001.mp4",
  "thumbnail_path": "thumbnails/scene_001.jpg",
  "version": 4,
  "url": "/api/v1/files/{project}/videos/scene_001.mp4"
}
```

**为什么不挂在 `files.py` 的通用 upload 接口**：通用 upload 的入参语义是"上传一个资产文件"，不带"绑定到具体 shot + 走 VersionManager + 发事件"的语义。新端点更贴近 shot 级动作。`files.py::ALLOWED_EXTENSIONS` 不变。

### 6.2 前端

`VideoImportModal.tsx`：

- 拖拽 + 点击的文件选择区（复用 `AssetFormModal` 的 input 模式，但只一个文件）
- 客户端先验 `accept="video/mp4,video/quicktime,video/webm"` + 200 MiB size
- 上传中显示进度条（`XHR.upload.onprogress`，因为 `fetch` 上传进度不友好）
- 成功后调用 store 的 `refreshProject()` 触发 SSE 已经覆盖到的字段刷新（或显式 setState）

新 API 方法：

```ts
static async importExternalVideo(
  projectName: string, episode: number, segmentId: string, file: File,
  onProgress?: (loaded: number, total: number) => void
): Promise<ImportVideoResultDTO> { ... }
```

走 XHR 实现以拿到进度回调；与现有 `API.request`（fetch 包装）并存。

## 7. Feature C：小云雀视频后端

### 7.1 关键事实（来自用户提供的"有参考-接口文档"）

| 项 | 取值 |
|---|---|
| 接口域名 | `https://visual.volcengineapi.com` |
| 鉴权 | Volcengine Signature v4（Header；`Region=cn-north-1`、`Service=cv`）|
| 提交 Action | `CVSync2AsyncSubmitTask`，Version=`2022-08-31` |
| 提交 method | `POST`，`Content-Type: application/json` |
| 「有参考」req_key | `pippit_iv2v_v20_cvtob_with_vinput` |
| 「无参考」req_key | **TODO**（用户提供文档中无） |
| 参考图字段 | `img_url_list: string[]`（图片+视频合计 ≤ 50；图片 ≤ 20MB、≤ 4096×4096；视频 ≤ 200MB、≤ 3min）|
| 必须是公网 URL | 是；不接受 base64 |
| 其它字段 | `prompt`（≤ 2000）、`ratio`（"16:9" / "9:16" / "4:3" / "3:4"）、`duration`（"~15s" / "~30s" / "40~60s"）、`language`（"Chinese" 默认 / "English" / ...）、`enable_watermark`（bool，默认 true，**ArcReel 必须传 false**）|
| 提交返回 | `{ task_id: string }` |
| 查询 Action | **CVSync2AsyncGetResult**（猜测；按 Volcengine "Visual" 服务一贯命名。**TODO**：实施前用真实 AK/SK 探活确认）|
| 查询返回 | `{ code, data: { status, video_url, resp_data: {InputVideoDurationSum, Duration}, aigc_meta_tagged } }` |
| status 枚举 | `processing` / `in_queue` / `generating` / `done` / `not_found` / `expired` |
| video_url TTL | 1 小时 |

### 7.2 模块拆分

#### `lib/volc_visual_shared.py`

唯一职责：对 `visual.volcengineapi.com` 出的请求做 Sig v4 签名，返回签名后的 Header 集合。

```python
def sign_request(
    *, method: str, host: str, path: str, query: dict[str, str],
    headers: dict[str, str], body: bytes,
    access_key: str, secret_key: str,
    region: str = "cn-north-1", service: str = "cv",
    timestamp: datetime | None = None,
) -> dict[str, str]:
    """返回追加了 Authorization + X-Date 等签名 header 的完整 header dict。"""
```

实现要点：

- 用 `hashlib.sha256` + `hmac.new` 手实现 sigv4（不引入新依赖）；参考火山官方 Python sig v4 范例
- `timestamp` 注入以便测试可控
- 测试 fixture：用文档/官方 SDK 的样例向量做回归（**TODO**：实施时拉一个官方样例做 golden test）

#### `lib/volc_tos_uploader.py`

```python
class TosImageUploader:
    def __init__(self, *, ak: str, sk: str, endpoint: str, bucket: str, region: str): ...
    async def upload_image(self, local_path: Path, *, key_prefix: str = "arcreel-ref/") -> str:
        """PUT 到 TOS，返回 1 小时过期的预签名 GET URL。"""
```

实现：

- 直接用 `httpx.AsyncClient` 走 TOS 的 S3 兼容 API（PUT object + 预签名 URL 算法）；不强制依赖 `tos` SDK，避免新依赖体积
- key 命名：`{key_prefix}{sha256(local_path bytes)[:16]}{ext}`，命名内容寻址 → **同图二次上传可幂等命中**（HEAD 命中直接生成新签名 URL）
- 预签名 URL 用 sigv4 GET 形态，TTL 3600s
- 失败重试：复用 `lib/retry.with_retry_async`

#### `lib/video_backends/volc_xiaoyunque.py`

```python
class VolcXiaoyunqueBackend:
    DEFAULT_MODEL = "xiaoyunque-agent-2.0"  # 内部标识，不是火山的真实 model 字段
    REQ_KEY_WITH_REFS = "pippit_iv2v_v20_cvtob_with_vinput"
    REQ_KEY_WITHOUT_REFS = "TODO_fill_after_doc_confirmed"

    def __init__(self, *, access_key, secret_key,
                 tos_endpoint, tos_bucket, tos_region, **_): ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        ref_urls = await self._upload_refs(request)         # TOS 预签名 URL
        task_id = await self._submit(request, ref_urls)    # 自动选 req_key
        return await self._poll_until_done(task_id, request)
```

要点：

- **req_key 分发**：若 `ref_urls` 非空 → `REQ_KEY_WITH_REFS`；否则 `REQ_KEY_WITHOUT_REFS`
- **ratio 映射**：直接透传 ArcReel 的 `aspect_ratio`（"16:9" / "9:16" / "4:3" / "3:4"）；不支持的 ratio 报错早返
- **duration 映射**：
  - request.duration_seconds ≤ 15 → "~15s"
  - 15 < ≤ 30 → "~30s"
  - 30 < ≤ 60 → "40~60s"
  - > 60 → 抛 `ValueError`
- **language**：首版固定 `"Chinese"`，留 `provider.optional_keys` 后续可配
- **enable_watermark**：固定 `false`
- **轮询**：复用 `lib/video_backends/base.py::poll_with_retry`；间隔 15s、上限 1200s
- **下载**：拿到 `video_url` 后复用 `download_video`
- **capabilities**：`{TEXT_TO_VIDEO, IMAGE_TO_VIDEO}`；`video_capabilities = VideoCapabilities(first_frame=True, reference_images=True, max_reference_images=50)`

### 7.3 Provider 注册

`lib/providers.py`：

```python
PROVIDER_VOLC_XIAOYUNQUE = "volc-xiaoyunque"
```

`lib/config/registry.py` 加：

```python
"volc-xiaoyunque": ProviderMeta(
    display_name="火山小云雀",
    description="火山引擎智能生视频 Agent 2.0，支持有参考图与纯文本两种模式，"
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
            resolutions=[],  # 模型自适应
        ),
    },
    default_base_url="https://visual.volcengineapi.com",
),
```

`lib/video_backends/__init__.py` 加：

```python
from lib.video_backends.volc_xiaoyunque import VolcXiaoyunqueBackend
register_backend(PROVIDER_VOLC_XIAOYUNQUE, VolcXiaoyunqueBackend)
```

`lib/generation_worker.py` 池配置追加 `'volc-xiaoyunque': (5, 3)`。

### 7.4 配置 / WebUI

- WebUI 设置页（`frontend/src/pages/Settings*`）自动识别 registry，**无需**前端改造即可弹出 5 个表单字段：`access_key / secret_key`（密文）/ `tos_endpoint / tos_bucket / tos_region`（明文）
- providers i18n：`lib/i18n/{zh,en,vi}/providers.py` 加 display_name / description

### 7.5 费用

首版 cost calculator 只记录 `duration_seconds`，单价 placeholder（TODO 提交前与用户对齐计费）。

## 8. 数据模型变化

- **不动** `GeneratedAssets`（已有 `video_clip / video_thumbnail / video_uri`，足够）
- `VersionManager.add_version` 的 `metadata` dict 新增可选 `"source": "internal" | "external"` 与 `"original_filename"`；不会破坏现有读取（旧版本读出来缺这个字段即默认 `internal`）
- `project.json` 不改 schema

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| 复制 prompt — 镜头无 step1/storyboard | 返回 404 + `_t("video_prompt_bundle_not_ready")` |
| 复制 prompt — 参考图文件丢失 | 跳过该图（不致命），response 中省略 + 日志 warning |
| ZIP 下载 — 任一参考图缺失 | 仍写入 ZIP（缺失的跳过），README 标注 |
| 导入视频 — 扩展名不允许 | 400 + i18n key `external_video_unsupported_format` |
| 导入视频 — 大小超限 | 413 + i18n key `external_video_too_large` |
| 导入视频 — ffmpeg 不存在 | 500 + i18n key `ffmpeg_required` + 服务端日志记 `shutil.which("ffmpeg") = None` |
| 导入视频 — 容器转换失败 | 500 + 上下文（stderr 头 200 字）|
| 小云雀 — TOS 上传失败 | retry 3 次后 raise，task 状态置 failed，错误信息走现有 task error 通道 |
| 小云雀 — 签名失败 | raise；日志记 method + canonical request 头（**不要**记签名 key） |
| 小云雀 — `status = expired` | raise（`video_url` 已过期，需要重试整次任务） |

## 10. 测试

- `tests/test_video_prompt_resolver.py` — 用既有 fixture project，断言 prompt 字符串与 worker 实跑等价；refs 按预期顺序 + URL
- `tests/test_routers_shots_video_prompt.py` — 端点 200/404/zip header
- `tests/test_routers_shots_import_video.py` — multipart 上传 mp4 / mov（mock ffmpeg）/ webm / oversize / bad ext
- `tests/test_volc_visual_signer.py` — golden vector 回归（实施时填入官方样例）
- `tests/test_volc_tos_uploader.py` — mock httpx，验证 sigv4 header + PUT body
- `tests/test_volc_xiaoyunque_backend.py` — mock httpx + mock TOS uploader；验证 req_key 自动分发、duration 映射、status 轮询、过期错误传播
- `tests/test_i18n_consistency.py`（既有）— 自动校验 zh/en/vi 新 key 不漂移

CI 现状：coverage ≥ 80%，basedpyright 0 error，ruff line-length 120；新代码必须遵守。

## 11. 国际化

新增 i18n key 草案（zh 为准，en/vi 同步）：

**前端 `dashboard.ts`**：

| key | zh |
|---|---|
| `media_copy_prompt` | 复制 Prompt |
| `media_copy_prompt_hint` | 复制 prompt 与参考图，去外部平台生成 |
| `media_import_video` | 导入视频 |
| `media_import_video_hint` | 导入本地视频文件，作为新版本添加 |
| `video_prompt_modal_title` | 视频生成材料 |
| `video_prompt_modal_prompt_label` | 最终 Prompt |
| `video_prompt_modal_refs_label` | 参考图（{{count}} 张） |
| `video_prompt_modal_copy_all` | 复制全部 |
| `video_prompt_modal_copied` | 已复制 |
| `video_prompt_modal_download_zip` | 打包下载 ZIP |
| `video_prompt_modal_no_refs` | 该镜头无参考图 |
| `video_import_modal_title` | 导入外部视频 |
| `video_import_modal_select_file` | 选择视频文件 |
| `video_import_modal_drag_hint` | 拖拽 .mp4 / .mov / .webm 到这里 |
| `video_import_modal_uploading` | 上传中…… |
| `video_import_modal_replace_warning` | 导入后将作为一个新版本，原视频可在版本时间机里恢复 |
| `video_import_modal_success` | 视频已导入 |

**后端 `lib/i18n/{zh,en,vi}/`**：

- `errors.py`: `video_prompt_bundle_not_ready`、`external_video_unsupported_format`、`external_video_too_large`、`ffmpeg_required`、`volc_visual_signature_failed`、`volc_tos_upload_failed`、`volc_xiaoyunque_task_expired`
- `providers.py`: 新增 `volc-xiaoyunque` 的 `display_name`、`description`、字段 label

## 12. 实施顺序

实施计划（下一步 writing-plans 出具）建议按以下分组，**每组结束都跑 lint + typecheck + tests，避免一次堆太多**：

1. **重构**：抽出 `video_prompt_resolver.py`，让 worker 跑通现有测试不动
2. **Feature A 后端**：新路由 + ZIP；加单测
3. **Feature A 前端**：MediaCard 按钮 + Modal + api.ts
4. **Feature B 后端**：导入端点 + ffmpeg + VersionManager；加单测
5. **Feature B 前端**：导入 Modal + api.ts 进度上传
6. **Feature C 基础**：`volc_visual_shared` + `volc_tos_uploader` + 单测
7. **Feature C 后端 backend**：`volc_xiaoyunque.py` + provider 注册 + 单测；先用 mock httpx 跑通
8. **联调**：用户提供"无参考"文档 + 真实 AK/SK 后填 `REQ_KEY_WITHOUT_REFS` 与 `CVSync2AsyncGetResult` 验证

## 13. 已知 TODO（实施前需用户确认）

1. **小云雀「无参考」接口 `req_key`**——文档未提供。在第 8 步联调前必须补全
2. **查询 Action 名 `CVSync2AsyncGetResult`**——猜测命名，实施前要用 AK/SK 实际探活；如果不是这个名，spec 第 7.1 / 7.2 / 7.5 都要 patch
3. **TOS 桶必须与小云雀同账号**——文档隐含；spec 第 5.1 已声明，但需用户在配置页 UI 上加一条提示
4. **小云雀单价**——cost_calculator 接入需要价格表
5. **WebUI 上没有 `volc-xiaoyunque` 显式入口**——验证 settings 页能自动通过 registry 长出表单；若不能（页面有硬编码 provider 列表），加一个 TODO 子任务

## 14. 风险与回退

- **风险**：TOS 上传慢拖累整次视频生成 → 缓解：内容寻址 + 复用既上传，重复请求秒级返回
- **风险**：小云雀返回的 mp4 在 1h 内未下载完 → 缓解：拿到 `video_url` 后立即 `download_video`（已经是流式 + 重试）
- **风险**：导入视频被恶意构造（如超大 mov 占磁盘）→ 缓解：上传流式校验大小、容器转换跑在 ffmpeg 子进程（受沙箱保护）
- **回退**：三件事互不阻塞。任一失败只需 revert 对应 commit；provider registry 删条目即可下线小云雀
