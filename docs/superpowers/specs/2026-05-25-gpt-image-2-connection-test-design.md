# gpt-image-2 生图连通性测试 设计文档

- 日期：2026-05-25
- 状态：设计完成，待用户确认
- 灵感来源：[Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) 的 `account_test_service.testOpenAIImageAPIKey`

## 背景与动机

ArcReel 当前对自定义供应商（`/api/v1/custom-providers/{id}/test`）只跑 `client.models.list()`，能验证 API Key 可用，但无法回答用户最关心的问题：**"这个号到底能不能跑 gpt-image-2 生图？"**

很多 sub2api 一类的中转站会返一份漂亮的 model list，但实际生图时上游配额、模型映射、内容安全策略才是真正的卡点。用户希望在保存供应商之后，能像 sub2api 自带的测试按钮那样，一键发起一次最小的 gpt-image-2 生图请求并预览结果。

## 范围

**做：**
- 给自定义供应商 (`custom_providers`) 加一条新的端点：`POST /api/v1/custom-providers/{id}/test-image-generation`
- 后端直接 httpx 调 `POST {base_url}/v1/images/generations`（OpenAI 兼容 API Key 协议）
- 前端 `CustomProviderDetail.tsx` 增加「测试生图」按钮 + 一个测试弹窗，支持选模型、改 prompt、预览返回的 base64 图片
- 三语 i18n（zh/en/vi）

**不做：**
- Codex `/v1/responses` 上的 OAuth 路径（用户场景已通过 sub2api 中转拍平成 API Key，OAuth 走法工程量大十倍且非眼下刚需，留扩展位）
- 预置供应商（`/api/v1/providers/openai/test`）改造（自定义供应商先做，预置供应商若日后有需求复用 `image_probe` 即可）
- 落盘 / 历史记录（每次测试是一次性预览，不存）

## 架构总览

```
[前端 CustomProviderDetail]
   ┌── 现有「测试连接」按钮（不变，跑 models.list）
   └── 新增「测试生图」按钮
         ↓ 点击弹出 ImageGenerationTestModal
              ↓ POST /api/v1/custom-providers/{id}/test-image-generation
                  body: {model_id, prompt?}
                     ↓
[server/routers/custom_providers.py]
   ├── 校验 model_id 属于此 provider 且 endpoint ∈ openai-images*
   ├── 解密 base_url + api_key
   └── 调 lib/custom_provider/image_probe.probe_image_generation(...)
                                            ↓
                  [lib/custom_provider/image_probe.py]
                  ├── httpx.post {base_url}/v1/images/generations
                  ├── payload {model, prompt, n:1, response_format:"b64_json"}
                  ├── header Authorization: Bearer {api_key}
                  └── 解析 data[0].{b64_json, revised_prompt}
                       ↓
                  ImageProbeResult dataclass
                       ↓
   → ImageGenerationTestResponse
     {success, message i18n, latency_ms, status_code, image_data_url, revised_prompt, model}
```

**关键设计选择：**

1. **新建独立模块 `lib/custom_provider/image_probe.py`** —— 不污染现有 `_test_openai`（那是 SDK 路径，只做 models.list）。
2. **httpx 直调，不走 openai SDK** —— 与 `lib/config/anthropic_probe.py` 同 pattern。SDK 路径冷启动慢、stderr 不含 HTTP status、诊断精度差；httpx 直调能拿到精确 status_code 和上游错误 body。
3. **复用 `ENDPOINT_REGISTRY` 校验** —— model 必须是 `media_type=image` **且 `image_capabilities` 包含 `TEXT_TO_IMAGE`** 的 endpoint，否则 422。即接受 `openai-images`（T2I+I2I）和 `openai-images-generations`（T2I），拒绝 `openai-images-edits`（I2I only，本测试不传参考图无法调用）。

## 后端 API

### 端点

```
POST /api/v1/custom-providers/{provider_id}/test-image-generation
Auth: CurrentUser
```

### Request

```json
{
  "model_id": "gpt-image-2",
  "prompt": "..."
}
```

- `model_id` 必填，必须是此 provider 下已配置且 `endpoint` 属于 image 类的模型
- `prompt` 选填；缺省时使用后端 i18n 默认 prompt
- `prompt` 上限 1000 字符（前端裁断，后端再校一次）

### Response

```json
{
  "success": true,
  "message": "生图测试成功",
  "latency_ms": 12340,
  "status_code": 200,
  "image_data_url": "data:image/png;base64,iVBORw0KGgo...",
  "revised_prompt": "A cute orange cat astronaut...",
  "model": "gpt-image-2"
}
```

失败时：

```json
{
  "success": false,
  "message": "API 返回 401: invalid_api_key",
  "latency_ms": 320,
  "status_code": 401,
  "image_data_url": null,
  "revised_prompt": null,
  "model": "gpt-image-2"
}
```

| HTTP | 含义 |
|---|---|
| 200 | 测试结果（success 字段区分成功/失败） |
| 404 | provider 不存在 / model 不存在 |
| 422 | model_id 的 endpoint 不是 image 类 |
| 401 | 未登录（沿用 CurrentUser 依赖） |

注意：**真正的"上游失败"也返 200**，由 body 的 `success: false` 表达。这与现有 `_run_connection_test` 风格一致，避免把"业务测试结果"挤进 HTTP 状态码。

### `lib/custom_provider/image_probe.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ImageProbeResult:
    success: bool
    status_code: int | None
    latency_ms: int
    image_b64: str | None
    mime_type: str           # 默认 "image/png"
    revised_prompt: str | None
    error: str | None         # 截断 200 字符

async def probe_image_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_s: float = 60.0,
) -> ImageProbeResult:
    """POST {base_url}/v1/images/generations，payload n=1, response_format=b64_json。

    日志严格只打 model + status_code + latency_ms，不打 base64 / api_key / prompt。
    错误体截断 200 字符。
    """
```

判定规则：
- 2xx 且 `data[0].b64_json` 非空 → success
- 2xx 但 `data=[]` 或缺字段 → 失败（专门 error msg：可能触发内容安全过滤）
- 非 2xx → 失败，error = 上游 body 截 200 字符
- timeout / 网络异常 → success=False, status_code=None

### 日志与安全

- 日志严格只打 `model + status_code + latency_ms`（与 `anthropic_probe.py` 同 pattern）
- 不打 base64、不打 api_key、不打 prompt
- 错误体截断 200 字符
- timeout: 60s（生图比 list-models 慢一个数量级）
- base64 图片**不落盘**，仅一次性响应给前端，HTTP 响应结束后内存即释放

### i18n keys

在 `lib/i18n/{zh,en,vi}/providers.py` 新增：

| key | zh | en | vi |
|---|---|---|---|
| `image_test_default_prompt` | （见下） | （见下） | （见下） |
| `image_test_success` | 生图测试成功 | Image generation test succeeded | Kiểm tra tạo ảnh thành công |
| `image_test_failed` | 生图测试失败：{err_msg} | Image generation failed: {err_msg} | Tạo ảnh thất bại: {err_msg} |
| `image_test_no_image_returned` | 上游返回 200 但 data 为空（可能触发内容安全过滤或上游异常） | Upstream returned 200 with empty data (likely content filter or upstream issue) | Upstream trả 200 nhưng data rỗng (có thể bị lọc nội dung hoặc lỗi upstream) |
| `image_test_model_not_t2i_endpoint` | model_id={model_id} 不是支持文生图的端点 | model_id={model_id} does not support text-to-image | model_id={model_id} không hỗ trợ text-to-image |
| `image_test_timeout` | 生图测试超时（60s） | Image generation test timed out (60s) | Hết thời gian kiểm tra tạo ảnh (60s) |

默认 prompt（短、可预期、高识别度，便于一眼判定是否真生成对了）：

- **zh/en/vi 三语统一使用英文** prompt：`"A cute orange cat astronaut sticker on a clean pastel background."`（gpt-image-2 对英文 prompt 表现最稳定，避免语种导致的"成功了但生成质量奇差"的误判）

### 校验逻辑（router）

```python
provider = await repo.get_provider(provider_id)
if provider is None:
    raise HTTPException(404, _t("provider_not_found"))

model = await repo.get_model_by_id(provider_id, body.model_id)
if model is None:
    raise HTTPException(404, _t("model_not_found"))

spec = ENDPOINT_REGISTRY.get(model.endpoint)
if (
    spec is None
    or spec.media_type != "image"
    or not spec.image_capabilities
    or ImageCapability.TEXT_TO_IMAGE not in spec.image_capabilities
):
    # 拒绝 endpoint=openai-images-edits 这种 I2I-only 模型（本测试不带参考图）
    raise HTTPException(422, _t("image_test_model_not_t2i_endpoint", model_id=body.model_id))

prompt = (body.prompt or "").strip() or _t("image_test_default_prompt")
prompt = prompt[:1000]  # 双重保险

result = await probe_image_generation(
    base_url=provider.base_url,
    api_key=provider.api_key,
    model=body.model_id,
    prompt=prompt,
)
return _serialize_image_probe(result, model_id=body.model_id, _t=_t)
```

## 前端 UI

### 文件改动

- 改：`frontend/src/components/pages/settings/CustomProviderDetail.tsx`（加按钮）
- 新增：`frontend/src/components/pages/settings/ImageGenerationTestModal.tsx`（弹窗）
- 改：`frontend/src/api.ts`（加 `testCustomProviderImageGeneration` 调用）
- 改：`frontend/src/i18n/{zh,en,vi}/dashboard.ts`（settings.imageTest.* 命名空间）

### 交互流程

1. 详情页 toolbar 显示「测试生图」按钮，位于「测试连接」之后
2. 当此 provider 没有任何 T2I 端点的模型（即 `openai-images` / `openai-images-generations`）时，按钮 disabled + tooltip 提示「先在模型列表配置一个支持文生图的模型」
3. 点击弹出 Modal：

   ```
   ┌──────────────────── 测试生图能力 ────────────────────┐
   │                                                     │
   │  模型: [▼ gpt-image-2                          ]    │
   │       (列出此 provider 下 endpoint 为 image 的       │
   │        模型, 默认选 model_id 含 gpt-image 的)        │
   │                                                     │
   │  Prompt: ┌──────────────────────────────────┐       │
   │          │ A cute orange cat astronaut       │       │
   │          │ sticker on a clean pastel back... │       │
   │          └──────────────────────────────────┘       │
   │          (1000 字符上限, 默认预填, 可清空改写)        │
   │                                                     │
   │  ⚠ 每次测试会真实计费 (~$0.04/张)                    │
   │                                                     │
   │             [Cancel]  [开始测试]                     │
   └─────────────────────────────────────────────────────┘
   ```

4. 点「开始测试」→ 按钮变 loading「测试中... (通常 10-30s)」+ 全弹窗 disable
5. 成功 → 弹窗内容切换为：

   ```
   ┌──────────────────── 测试生图能力 ────────────────────┐
   │  ✓ 生图测试成功 · 12.3s                              │
   │  ┌─────────────────────────────────────────────┐    │
   │  │                                             │    │
   │  │              <img base64 预览>              │    │
   │  │                                             │    │
   │  └─────────────────────────────────────────────┘    │
   │                                                     │
   │  ▶ Revised prompt (可折叠展开)                       │
   │                                                     │
   │             [关闭]  [再测一次]                       │
   └─────────────────────────────────────────────────────┘
   ```

6. 失败 → 弹窗顶部红框：`✗ {message}` + `HTTP {status_code} · {latency_ms}ms` + `[关闭]` + `[再试一次]`

### 边缘情况

- prompt 超 1000 字符 → onChange 中裁断 + 红字提示
- 后端返回 `success=false` 但 `image_data_url` 非空（理论上不会，但防御）→ 视为失败
- 用户取消弹窗时不取消已发出的请求（请求完了不影响）

## 测试矩阵

### `tests/test_custom_provider_image_probe.py`（新增）

mock `httpx` 验证：

- **成功路径**：返回 200 + `{data: [{b64_json:"...", revised_prompt:"..."}]}` → `ImageProbeResult.success=True`
- **401 / 403** → success=False, status_code 对应，error 含上游 body 截断
- **404 / 429 / 500** → success=False，error 截断
- **200 但 `data=[]`** → success=False, error="upstream returned empty data"
- **200 但 JSON 不含 data 字段** → success=False
- **200 但 JSON 解析失败** → success=False
- **httpx.TimeoutException** → success=False, status_code=None
- **httpx.HTTPError**（连接拒绝等）→ success=False, status_code=None
- **日志断言**：日志记录不含 base64 / api_key / prompt 任何字符
- **错误体截断**：上游返回 5KB 错误 body → error 长度 ≤ 203（200 + "…"）

### `tests/test_custom_providers_api.py`（扩展）

- happy path：mock `probe_image_generation` 返回成功 → 200 + `success=true` + image_data_url
- model_id 不存在 → 404
- model 非 T2I 端点（如 `openai-images-edits`）→ 422
- provider 不存在 → 404
- 不传 prompt → 后端用默认 prompt
- prompt 超长 → 后端裁断到 1000 字符

### `tests/test_i18n_consistency.py`

无需改动；自动验证三语 key 不漂移。

### 前端

暂不写单测，与现有 `CustomProviderDetail.tsx` 风格一致；如后续要加，弹窗交互逻辑用 vitest 测 hooks。

## 文件清单

| 类型 | 路径 | 改动 |
|---|---|---|
| 新增 | `lib/custom_provider/image_probe.py` | 核心 probe 函数 |
| 改 | `server/routers/custom_providers.py` | 加 router endpoint + 校验 |
| 改 | `lib/i18n/zh/providers.py` | 6 个 key |
| 改 | `lib/i18n/en/providers.py` | 6 个 key |
| 改 | `lib/i18n/vi/providers.py` | 6 个 key |
| 新增 | `tests/test_custom_provider_image_probe.py` | 单测 |
| 改 | `tests/test_custom_providers_api.py` | router 端到端 |
| 新增 | `frontend/src/components/pages/settings/ImageGenerationTestModal.tsx` | UI 弹窗 |
| 改 | `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | 加按钮 |
| 改 | `frontend/src/api.ts` | 加 API 调用 |
| 改 | `frontend/src/i18n/zh/dashboard.ts` | settings.imageTest.* |
| 改 | `frontend/src/i18n/en/dashboard.ts` | settings.imageTest.* |
| 改 | `frontend/src/i18n/vi/dashboard.ts` | settings.imageTest.* |

## 已知风险与权衡

1. **生图真实计费** —— 每次点击就是真金白银（gpt-image-2 ~$0.04/张），弹窗内会显式提示。
2. **base64 内联响应体大** —— 1024×1024 png base64 约 1-3MB；HTTP 一次性吞下没问题，但日志和监控要避开 body。
3. **timeout 60s 是经验值** —— 慢的中转可能更慢；首版不引入配置项（YAGNI），实战发现不够再说。
4. **OAuth `/v1/responses` 路径暂不做** —— 与用户确认；若将来要做，新加 `image_probe_responses.py` 即可，本设计未堵死扩展位。
5. **revised_prompt 可空** —— sub2api 中转有的会返，OpenAI 原生 gpt-image 不一定返；前端按"可空"处理。
6. **预置 OpenAI 供应商暂不做** —— 自定义供应商先做，预置供应商若日后需要，复用 `image_probe` 即可。

## 扩展位（不在本次范围内）

- 历史测试记录：未来想看「上次测试时间 / 状态 / 缩略图」时，把 ImageProbeResult 写入 `image_test_history` 表。
- OAuth Codex `/responses` 路径：见 sub2api `testOpenAIImageOAuth`，需要处理 SSE 流式 + `image_generation` tool 协议 + chatgpt.com 专属 header。
- 预置 OpenAI 供应商「测试生图」：复用 `image_probe.probe_image_generation`，在 `providers.py::_test_openai` 旁新增 endpoint。
