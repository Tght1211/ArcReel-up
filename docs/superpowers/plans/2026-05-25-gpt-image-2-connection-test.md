# gpt-image-2 生图连通性测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给自定义供应商（OpenAI 兼容）加一个独立的「测试生图」按钮，后端 httpx 直调 `POST /v1/images/generations`，前端弹窗预览返回的 base64 PNG。

**Architecture:** 新建 `lib/custom_provider/image_probe.py` 实现 httpx 直调（与 `lib/config/anthropic_probe.py` 同 pattern，不走 OpenAI SDK），暴露 `probe_image_generation()` async 函数。在 `server/routers/custom_providers.py` 加一条 `POST /api/v1/custom-providers/{id}/test-image-generation` 端点，校验 model 属于 T2I 类后调用 probe。前端在 `CustomProviderDetail.tsx` toolbar 旁加按钮，弹出 `ImageGenerationTestModal` 收集 prompt + 模型，调 API 后内联预览 base64 图。

**Tech Stack:** Python 3.14 + FastAPI + httpx · React 19 + TypeScript + Tailwind · pytest + vitest · i18next (zh/en/vi)

**Spec reference:** `docs/superpowers/specs/2026-05-25-gpt-image-2-connection-test-design.md`

**Empirical contract (实测于 spec 撰写阶段):**
- 上游 `POST {base_url}/v1/images/generations` 接收 `{model, prompt, n, response_format:"b64_json"}`，返回 `{data:[{b64_json, revised_prompt}], usage, model, output_format, size, quality, background, created}`
- 实测延迟 ≈ 62s，故 timeout = 120s
- PNG magic `89504e470d0a1a0a` 验证

---

## File Structure

| Path | Responsibility | New / Modify |
|---|---|---|
| `lib/custom_provider/image_probe.py` | httpx 直调上游 `/v1/images/generations`，返回 `ImageProbeResult` dataclass | New |
| `server/routers/custom_providers.py` | 加 `POST /{provider_id}/test-image-generation` 端点 + 请求/响应 Pydantic 模型 | Modify |
| `lib/i18n/zh/errors.py` | 加 6 个 image_test_* key | Modify |
| `lib/i18n/en/errors.py` | 同上（en 翻译） | Modify |
| `lib/i18n/vi/errors.py` | 同上（vi 翻译） | Modify |
| `tests/test_custom_provider_image_probe.py` | probe 函数单测（mock httpx） | New |
| `tests/test_custom_providers_api.py` | 端点端到端测试（mock probe） | Modify |
| `frontend/src/api.ts` | 加 `testCustomProviderImageGeneration` 方法 | Modify |
| `frontend/src/components/pages/settings/ImageGenerationTestModal.tsx` | 弹窗组件：选模型 / 填 prompt / 预览图 / 错误展示 | New |
| `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | toolbar 加按钮 + 渲染 Modal | Modify |
| `frontend/src/i18n/zh/dashboard.ts` | 12 个 `image_test_*` 翻译 key | Modify |
| `frontend/src/i18n/en/dashboard.ts` | 同上 | Modify |
| `frontend/src/i18n/vi/dashboard.ts` | 同上 | Modify |

---

## Task 1: `ImageProbeResult` dataclass + 模块骨架

**Files:**
- Create: `lib/custom_provider/image_probe.py`
- Test: `tests/test_custom_provider_image_probe.py`

- [ ] **Step 1.1: 写 dataclass 形状测试**

Create `tests/test_custom_provider_image_probe.py`:

```python
"""lib/custom_provider/image_probe.py 单测：mock httpx 验证 probe 行为。

设计契约见 docs/superpowers/specs/2026-05-25-gpt-image-2-connection-test-design.md。
"""

from __future__ import annotations

from lib.custom_provider.image_probe import ImageProbeResult


class TestImageProbeResultShape:
    def test_dataclass_fields(self):
        r = ImageProbeResult(
            success=True,
            status_code=200,
            latency_ms=12345,
            image_b64="iVBOR...",
            mime_type="image/png",
            revised_prompt="A cat",
            error=None,
            upstream_model="gpt-image-2",
            upstream_size="auto",
            upstream_quality="auto",
            upstream_output_format="png",
        )
        assert r.success is True
        assert r.latency_ms == 12345
        assert r.image_b64 == "iVBOR..."
        assert r.upstream_output_format == "png"

    def test_dataclass_frozen(self):
        import dataclasses
        r = ImageProbeResult(
            success=False, status_code=None, latency_ms=0, image_b64=None,
            mime_type="image/png", revised_prompt=None, error="boom",
            upstream_model=None, upstream_size=None, upstream_quality=None,
            upstream_output_format=None,
        )
        try:
            r.success = True  # type: ignore[misc]
            raise AssertionError("expected frozen dataclass to reject mutation")
        except dataclasses.FrozenInstanceError:
            pass
```

- [ ] **Step 1.2: 跑测试确认 FAIL**

Run: `uv run pytest tests/test_custom_provider_image_probe.py -v`
Expected: `ImportError: cannot import name 'ImageProbeResult'`

- [ ] **Step 1.3: 写 dataclass + 模块文档**

Create `lib/custom_provider/image_probe.py`:

```python
"""自定义供应商的 OpenAI 兼容图片生成探针 (POST /v1/images/generations)。

与 lib/config/anthropic_probe.py 同 pattern：httpx 直调，不走 SDK。
SDK 路径冷启动慢、stderr 不含 HTTP status、诊断精度差；httpx 能拿到精确
status_code 和上游错误 body，分类更可靠。

日志严格只打 model + status_code + latency_ms，不打 base64 / api_key / prompt。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageProbeResult:
    """单次生图测试的结果。失败时 image_b64 / revised_prompt / upstream_* 字段可能为 None。"""

    success: bool
    status_code: int | None
    latency_ms: int
    image_b64: str | None
    mime_type: str  # 默认 "image/png"; 若上游返回 output_format 则按其推导
    revised_prompt: str | None
    error: str | None  # 截断到 200 字符
    # 上游 metadata（仅展示, sub2api 中转会返, 其它 OpenAI 兼容上游不保证）
    upstream_model: str | None
    upstream_size: str | None
    upstream_quality: str | None
    upstream_output_format: str | None
```

- [ ] **Step 1.4: 跑测试确认 PASS**

Run: `uv run pytest tests/test_custom_provider_image_probe.py -v`
Expected: 2 passed

- [ ] **Step 1.5: ruff + basedpyright**

Run:
```bash
uv run ruff check lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run ruff format lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run basedpyright lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
```
Expected: 0 errors

- [ ] **Step 1.6: Commit**

```bash
git add lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
git commit -m "feat(custom_provider): add ImageProbeResult dataclass skeleton"
```

---

## Task 2: `probe_image_generation()` happy path (success)

**Files:**
- Modify: `lib/custom_provider/image_probe.py`
- Modify: `tests/test_custom_provider_image_probe.py`

- [ ] **Step 2.1: 写 happy path 测试**

Append to `tests/test_custom_provider_image_probe.py`:

```python
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _make_resp(*, status_code: int, json_body: dict | None = None, text: str | None = None) -> httpx.Response:
    """构造 httpx.Response 用作 mock 返回值，request 字段必填否则部分属性会 raise。"""
    req = httpx.Request("POST", "https://upstream.test/v1/images/generations")
    if json_body is not None:
        import json as _json
        return httpx.Response(status_code, request=req, content=_json.dumps(json_body).encode())
    return httpx.Response(status_code, request=req, text=text or "")


class TestProbeImageGenerationHappyPath:
    @pytest.mark.asyncio
    async def test_success_returns_image_and_metadata(self):
        from lib.custom_provider import image_probe

        upstream_body = {
            "data": [{"b64_json": "iVBORw0KGgoAAAA", "revised_prompt": "A cute cat sticker"}],
            "model": "gpt-image-2",
            "output_format": "png",
            "size": "1024x1024",
            "quality": "high",
            "usage": {"input_tokens": 64, "output_tokens": 1372},
        }
        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body=upstream_body))

        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://upstream.test",
                api_key="sk-test",
                model="gpt-image-2",
                prompt="A cat",
            )

        assert r.success is True
        assert r.status_code == 200
        assert r.image_b64 == "iVBORw0KGgoAAAA"
        assert r.revised_prompt == "A cute cat sticker"
        assert r.mime_type == "image/png"
        assert r.upstream_model == "gpt-image-2"
        assert r.upstream_size == "1024x1024"
        assert r.upstream_quality == "high"
        assert r.upstream_output_format == "png"
        assert r.error is None
        assert r.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_post_called_with_correct_url_payload_and_headers(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(
            status_code=200,
            json_body={"data": [{"b64_json": "x"}]},
        ))

        with patch.object(image_probe, "_post", mock_post):
            await image_probe.probe_image_generation(
                base_url="https://upstream.test/",  # 带尾斜杠
                api_key="sk-abc",
                model="gpt-image-2",
                prompt="hello",
            )

        kwargs = mock_post.call_args.kwargs
        assert kwargs["url"] == "https://upstream.test/v1/images/generations"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-abc"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["payload"] == {
            "model": "gpt-image-2",
            "prompt": "hello",
            "n": 1,
            "response_format": "b64_json",
        }
        assert kwargs["timeout_s"] == 120.0
```

- [ ] **Step 2.2: 跑测试确认 FAIL**

Run: `uv run pytest tests/test_custom_provider_image_probe.py::TestProbeImageGenerationHappyPath -v`
Expected: `AttributeError: module ... has no attribute 'probe_image_generation'`

- [ ] **Step 2.3: 实现 `_post` indirection + happy path**

Append to `lib/custom_provider/image_probe.py`:

```python
import logging
import time
from typing import Any

import httpx

from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200
_DEFAULT_TIMEOUT_S = 120.0  # 实测 sub2api 中转 gpt-image-2 ~62s，留 2x buffer


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> httpx.Response:
    """间接层：测试时 patch 这一个（与 anthropic_probe._post 同 pattern）。"""
    client = get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"


def _mime_from_output_format(output_format: str | None) -> str:
    """把上游 output_format 字符串映射到 MIME，未知或缺失时回退 image/png。"""
    if not output_format:
        return "image/png"
    fmt = output_format.lower().strip()
    if fmt in ("png", "image/png"):
        return "image/png"
    if fmt in ("jpeg", "jpg", "image/jpeg"):
        return "image/jpeg"
    if fmt in ("webp", "image/webp"):
        return "image/webp"
    return "image/png"


async def probe_image_generation(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> ImageProbeResult:
    """POST {base_url}/v1/images/generations 发最小请求 (n=1, response_format=b64_json)。

    判定:
    - 2xx + data[0].b64_json 非空 → success
    - 2xx 但 data=[] / 缺字段 / JSON 解析失败 → 失败 (error 含原因)
    - 非 2xx → 失败 (上游 body 截 200 字符)
    - timeout / 网络异常 → 失败 (status_code=None)

    日志只打 model + status_code + latency_ms，绝不打 base64 / api_key / prompt。
    """
    url = f"{base_url.rstrip('/')}/v1/images/generations"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    elapsed = int((time.perf_counter() - started) * 1000)

    logger.info("probe_image_generation model=%s status=%d elapsed_ms=%d", model, resp.status_code, elapsed)

    if resp.status_code >= 400:
        return ImageProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            image_b64=None,
            mime_type="image/png",
            revised_prompt=None,
            error=_truncate(resp.text),
            upstream_model=None,
            upstream_size=None,
            upstream_quality=None,
            upstream_output_format=None,
        )

    try:
        body = resp.json()
    except ValueError:
        return ImageProbeResult(
            success=False, status_code=resp.status_code, latency_ms=elapsed,
            image_b64=None, mime_type="image/png", revised_prompt=None,
            error="upstream returned non-JSON body",
            upstream_model=None, upstream_size=None, upstream_quality=None, upstream_output_format=None,
        )

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        return ImageProbeResult(
            success=False, status_code=resp.status_code, latency_ms=elapsed,
            image_b64=None, mime_type="image/png", revised_prompt=None,
            error="upstream returned 200 with empty data (likely content filter or upstream issue)",
            upstream_model=None, upstream_size=None, upstream_quality=None, upstream_output_format=None,
        )

    first = data[0] if isinstance(data[0], dict) else {}
    b64 = first.get("b64_json") if isinstance(first.get("b64_json"), str) else None
    if not b64:
        return ImageProbeResult(
            success=False, status_code=resp.status_code, latency_ms=elapsed,
            image_b64=None, mime_type="image/png", revised_prompt=None,
            error="upstream data[0] missing b64_json field",
            upstream_model=None, upstream_size=None, upstream_quality=None, upstream_output_format=None,
        )

    output_format = body.get("output_format") if isinstance(body.get("output_format"), str) else None
    return ImageProbeResult(
        success=True,
        status_code=resp.status_code,
        latency_ms=elapsed,
        image_b64=b64,
        mime_type=_mime_from_output_format(output_format),
        revised_prompt=first.get("revised_prompt") if isinstance(first.get("revised_prompt"), str) else None,
        error=None,
        upstream_model=body.get("model") if isinstance(body.get("model"), str) else None,
        upstream_size=body.get("size") if isinstance(body.get("size"), str) else None,
        upstream_quality=body.get("quality") if isinstance(body.get("quality"), str) else None,
        upstream_output_format=output_format,
    )
```

- [ ] **Step 2.4: 跑测试确认 PASS**

Run: `uv run pytest tests/test_custom_provider_image_probe.py::TestProbeImageGenerationHappyPath -v`
Expected: 2 passed

- [ ] **Step 2.5: ruff + basedpyright**

Run:
```bash
uv run ruff check lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run ruff format lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run basedpyright lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
```
Expected: 0 errors

- [ ] **Step 2.6: Commit**

```bash
git add lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
git commit -m "feat(custom_provider): add probe_image_generation happy path"
```

---

## Task 3: `probe_image_generation()` 错误分支

**Files:**
- Modify: `tests/test_custom_provider_image_probe.py`
- Modify: `lib/custom_provider/image_probe.py` (添加 try/except 处理网络异常)

- [ ] **Step 3.1: 写错误分支测试**

Append to `tests/test_custom_provider_image_probe.py`:

```python
class TestProbeImageGenerationErrors:
    @pytest.mark.asyncio
    async def test_401_returns_truncated_error(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(
            status_code=401, text='{"error":{"message":"invalid_api_key"}}',
        ))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="bad", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code == 401
        assert "invalid_api_key" in (r.error or "")
        assert r.image_b64 is None

    @pytest.mark.asyncio
    async def test_429_rate_limited(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=429, text="rate limited"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code == 429

    @pytest.mark.asyncio
    async def test_5xx_returns_status(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=502, text="bad gateway"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code == 502

    @pytest.mark.asyncio
    async def test_200_with_empty_data_fails(self):
        """sub2api 实测：内容安全过滤时上游可能返 200 + data:[]."""
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body={"data": []}))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code == 200
        assert "empty data" in (r.error or "")

    @pytest.mark.asyncio
    async def test_200_with_missing_data_field_fails(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, json_body={"foo": "bar"}))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False

    @pytest.mark.asyncio
    async def test_200_with_non_json_body_fails(self):
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(status_code=200, text="<html>oops</html>"))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert "non-JSON" in (r.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_status_none(self):
        from lib.custom_provider import image_probe

        async def _raise_timeout(**_kw):
            raise httpx.TimeoutException("read timeout")

        with patch.object(image_probe, "_post", AsyncMock(side_effect=_raise_timeout)):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code is None
        assert "timeout" in (r.error or "").lower()

    @pytest.mark.asyncio
    async def test_network_error_returns_status_none(self):
        from lib.custom_provider import image_probe

        async def _raise_conn(**_kw):
            raise httpx.ConnectError("conn refused")

        with patch.object(image_probe, "_post", AsyncMock(side_effect=_raise_conn)):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.success is False
        assert r.status_code is None

    @pytest.mark.asyncio
    async def test_error_body_truncated_to_200_chars(self):
        from lib.custom_provider import image_probe

        long_body = "x" * 5000
        mock_post = AsyncMock(return_value=_make_resp(status_code=500, text=long_body))
        with patch.object(image_probe, "_post", mock_post):
            r = await image_probe.probe_image_generation(
                base_url="https://x", api_key="k", model="gpt-image-2", prompt="p",
            )
        assert r.error is not None
        # 200 个 'x' + 1 个 '…' 字符 (注意: ellipsis 是单字符, 不是三点)
        assert len(r.error) <= 201
        assert r.error.endswith("…")

    @pytest.mark.asyncio
    async def test_log_does_not_contain_api_key_or_base64(self, caplog):
        """日志安全性: 不允许打 api_key 或 base64 内容."""
        import logging as _logging
        from lib.custom_provider import image_probe

        mock_post = AsyncMock(return_value=_make_resp(
            status_code=200,
            json_body={"data": [{"b64_json": "SECRETBASE64DATAXYZ"}]},
        ))
        with caplog.at_level(_logging.INFO, logger="lib.custom_provider.image_probe"):
            with patch.object(image_probe, "_post", mock_post):
                await image_probe.probe_image_generation(
                    base_url="https://x", api_key="sk-supersecret-key", model="gpt-image-2", prompt="hello",
                )
        full_log = " ".join(rec.getMessage() for rec in caplog.records)
        assert "sk-supersecret-key" not in full_log
        assert "SECRETBASE64DATAXYZ" not in full_log
```

- [ ] **Step 3.2: 跑测试确认 timeout/network 测试 FAIL（其他应已 PASS）**

Run: `uv run pytest tests/test_custom_provider_image_probe.py::TestProbeImageGenerationErrors -v`
Expected: `test_timeout_*` 和 `test_network_error_*` FAIL（因为 probe 还没 catch 异常），其他应 PASS。

- [ ] **Step 3.3: 加 try/except 兜底网络异常**

Edit `lib/custom_provider/image_probe.py`，找到 Task 2 step 2.3 写下的这段：

```python
    started = time.perf_counter()
    resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    elapsed = int((time.perf_counter() - started) * 1000)
```

替换为下面带 try/except 的版本：

```python
    started = time.perf_counter()
    try:
        resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_image_generation model=%s timeout elapsed_ms=%d", model, elapsed)
        return ImageProbeResult(
            success=False, status_code=None, latency_ms=elapsed,
            image_b64=None, mime_type="image/png", revised_prompt=None,
            error=_truncate(f"timeout: {exc!s}"),
            upstream_model=None, upstream_size=None, upstream_quality=None, upstream_output_format=None,
        )
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_image_generation model=%s network_err elapsed_ms=%d", model, elapsed)
        return ImageProbeResult(
            success=False, status_code=None, latency_ms=elapsed,
            image_b64=None, mime_type="image/png", revised_prompt=None,
            error=_truncate(str(exc)),
            upstream_model=None, upstream_size=None, upstream_quality=None, upstream_output_format=None,
        )
    elapsed = int((time.perf_counter() - started) * 1000)
```

- [ ] **Step 3.4: 跑全部错误分支测试**

Run: `uv run pytest tests/test_custom_provider_image_probe.py -v`
Expected: all passed

- [ ] **Step 3.5: ruff + basedpyright + 覆盖率**

Run:
```bash
uv run ruff check lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run ruff format lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run basedpyright lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
uv run pytest tests/test_custom_provider_image_probe.py --cov=lib.custom_provider.image_probe --cov-report=term-missing
```
Expected: 0 lint errors, ≥90% coverage on `image_probe.py`

- [ ] **Step 3.6: Commit**

```bash
git add lib/custom_provider/image_probe.py tests/test_custom_provider_image_probe.py
git commit -m "feat(custom_provider): probe_image_generation handles all error branches"
```

---

## Task 4: i18n keys (zh/en/vi)

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`
- Modify: `lib/i18n/vi/errors.py`

> 后端 i18n 全部加到 `errors.py`，与现有 `connection_success` / `connection_failed` / `provider_not_found` 同位置。

- [ ] **Step 4.1: 加 zh keys**

Edit `lib/i18n/zh/errors.py`，在合适位置（其它 connection_* 附近）加入：

```python
"image_test_default_prompt": "A cute orange cat astronaut sticker on a clean pastel background.",
"image_test_success": "生图测试成功",
"image_test_failed": "生图测试失败：{err_msg}",
"image_test_no_image_returned": "上游返回 200 但未生成图片（可能触发内容安全过滤或上游异常）",
"image_test_model_not_t2i_endpoint": "model_id={model_id} 不是支持文生图的端点",
"image_test_timeout": "生图测试超时（120s）",
```

- [ ] **Step 4.2: 加 en keys**

Edit `lib/i18n/en/errors.py`，同位置加入：

```python
"image_test_default_prompt": "A cute orange cat astronaut sticker on a clean pastel background.",
"image_test_success": "Image generation test succeeded",
"image_test_failed": "Image generation test failed: {err_msg}",
"image_test_no_image_returned": "Upstream returned 200 with no image (likely content filter or upstream issue)",
"image_test_model_not_t2i_endpoint": "model_id={model_id} does not support text-to-image",
"image_test_timeout": "Image generation test timed out (120s)",
```

- [ ] **Step 4.3: 加 vi keys**

Edit `lib/i18n/vi/errors.py`，同位置加入：

```python
"image_test_default_prompt": "A cute orange cat astronaut sticker on a clean pastel background.",
"image_test_success": "Kiểm tra tạo ảnh thành công",
"image_test_failed": "Kiểm tra tạo ảnh thất bại: {err_msg}",
"image_test_no_image_returned": "Upstream trả 200 nhưng không có ảnh (có thể bị lọc nội dung hoặc lỗi upstream)",
"image_test_model_not_t2i_endpoint": "model_id={model_id} không hỗ trợ text-to-image",
"image_test_timeout": "Hết thời gian kiểm tra tạo ảnh (120s)",
```

- [ ] **Step 4.4: 跑 i18n 一致性测试**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: passed (三语 key 集合一致)

- [ ] **Step 4.5: Commit**

```bash
git add lib/i18n/zh/errors.py lib/i18n/en/errors.py lib/i18n/vi/errors.py
git commit -m "feat(i18n): add image_test_* keys for gpt-image-2 connection test"
```

---

## Task 5: Backend 端点 — Pydantic 模型 + router 骨架

**Files:**
- Modify: `server/routers/custom_providers.py`
- Modify: `tests/test_custom_providers_api.py`

- [ ] **Step 5.1: 写端点 happy path 测试**

打开 `tests/test_custom_providers_api.py`，确认它使用的 fixtures（async client / DB session / repo 工厂）。在文件末尾追加：

```python
class TestImageGenerationEndpoint:
    """POST /api/v1/custom-providers/{id}/test-image-generation 端到端测试。

    所有测试都 patch lib.custom_provider.image_probe.probe_image_generation
    避免真的发 HTTP；probe 自身的单测见 tests/test_custom_provider_image_probe.py。
    """

    @pytest.mark.asyncio
    async def test_happy_path_returns_image_data_url(
        self, async_client, custom_provider_with_image_model, monkeypatch
    ):
        """provider + gpt-image-2 模型已存在 → 200 + image_data_url。"""
        from lib.custom_provider import image_probe

        async def _fake_probe(**_kw):
            return image_probe.ImageProbeResult(
                success=True, status_code=200, latency_ms=12345,
                image_b64="iVBORw0K", mime_type="image/png",
                revised_prompt="A cute cat", error=None,
                upstream_model="gpt-image-2", upstream_size="auto",
                upstream_quality="auto", upstream_output_format="png",
            )
        monkeypatch.setattr("server.routers.custom_providers.probe_image_generation", _fake_probe)

        provider = custom_provider_with_image_model
        resp = await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "gpt-image-2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200
        assert body["latency_ms"] == 12345
        assert body["image_data_url"] == "data:image/png;base64,iVBORw0K"
        assert body["revised_prompt"] == "A cute cat"
        assert body["model"] == "gpt-image-2"
        assert body["upstream_metadata"]["model"] == "gpt-image-2"
        assert body["upstream_metadata"]["output_format"] == "png"
```

> 如果现有 `test_custom_providers_api.py` 没有 `custom_provider_with_image_model` fixture，先在文件顶部 fixtures 区追加（基于现有 `custom_provider` fixture 复制改造）：
>
> ```python
> @pytest.fixture
> async def custom_provider_with_image_model(db_session):
>     """创建一个 OpenAI 兼容自定义供应商 + 一个 gpt-image-2 (T2I) 模型。"""
>     from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
>     provider = CustomProvider(
>         display_name="Test OpenAI Proxy",
>         discovery_format="openai",
>         base_url="https://upstream.test",
>         api_key="sk-test",
>     )
>     db_session.add(provider)
>     await db_session.flush()
>     model = CustomProviderModel(
>         provider_id=provider.id,
>         model_id="gpt-image-2",
>         display_name="GPT Image 2",
>         endpoint="openai-images-generations",
>         is_enabled=True,
>     )
>     db_session.add(model)
>     await db_session.commit()
>     await db_session.refresh(provider)
>     return provider
> ```
>
> 实施时请先 `grep -n "custom_provider" tests/test_custom_providers_api.py tests/conftest.py` 找现有 fixtures 形状，按现有命名风格命名。

- [ ] **Step 5.2: 跑测试确认 FAIL**

Run: `uv run pytest tests/test_custom_providers_api.py::TestImageGenerationEndpoint -v`
Expected: `404 Not Found`（端点还没注册）

- [ ] **Step 5.3: 在 `custom_providers.py` 顶部 import 增加 probe**

Edit `server/routers/custom_providers.py`，在 import 区加（注意：`ImageCapability` 已在文件顶部第 31 行 import，无需重复）：

```python
from lib.custom_provider.image_probe import ImageProbeResult, probe_image_generation
```

- [ ] **Step 5.4: 加 Pydantic 模型**

在 `custom_providers.py` 既有 Pydantic 模型区（找 `class ConnectionTestResponse` 附近）加：

```python
class ImageGenerationTestRequest(BaseModel):
    model_id: str
    prompt: str | None = None


class UpstreamMetadata(BaseModel):
    model: str | None = None
    size: str | None = None
    quality: str | None = None
    output_format: str | None = None


class ImageGenerationTestResponse(BaseModel):
    success: bool
    message: str
    latency_ms: int
    status_code: int | None
    image_data_url: str | None
    revised_prompt: str | None
    model: str
    upstream_metadata: UpstreamMetadata
```

- [ ] **Step 5.5: 加 router endpoint**

在文件末尾（紧跟现有 `test_provider_connection_by_id` 后）加：

```python
_MAX_PROMPT_CHARS = 1000


def _build_image_data_url(b64: str | None, mime: str) -> str | None:
    if not b64:
        return None
    return f"data:{mime};base64,{b64}"


def _serialize_image_probe(
    r: ImageProbeResult, *, model_id: str, _t: Callable[..., str]
) -> ImageGenerationTestResponse:
    if r.success:
        message = _t("image_test_success")
    elif r.status_code is None and r.error and "timeout" in r.error.lower():
        message = _t("image_test_timeout")
    elif r.status_code == 200 and "empty data" in (r.error or ""):
        message = _t("image_test_no_image_returned")
    else:
        message = _t("image_test_failed", err_msg=r.error or f"HTTP {r.status_code}")
    return ImageGenerationTestResponse(
        success=r.success,
        message=message,
        latency_ms=r.latency_ms,
        status_code=r.status_code,
        image_data_url=_build_image_data_url(r.image_b64, r.mime_type),
        revised_prompt=r.revised_prompt,
        model=model_id,
        upstream_metadata=UpstreamMetadata(
            model=r.upstream_model,
            size=r.upstream_size,
            quality=r.upstream_quality,
            output_format=r.upstream_output_format,
        ),
    )


@router.post(
    "/{provider_id}/test-image-generation",
    response_model=ImageGenerationTestResponse,
)
async def test_provider_image_generation(
    provider_id: int,
    body: ImageGenerationTestRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ImageGenerationTestResponse:
    """对自定义供应商发起一次 gpt-image-2 风格的生图测试。

    会真实调用上游 /v1/images/generations 端点（产生费用）。
    仅接受 endpoint 的 image_capabilities 含 TEXT_TO_IMAGE 的模型，
    拒绝 openai-images-edits 这种 I2I-only 端点（本接口不带参考图）。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))

    model = await repo.get_model_by_ids(provider_id, body.model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=_t("model_not_found"))

    spec = ENDPOINT_REGISTRY.get(model.endpoint)
    if (
        spec is None
        or spec.media_type != "image"
        or not spec.image_capabilities
        or ImageCapability.TEXT_TO_IMAGE not in spec.image_capabilities
    ):
        raise HTTPException(
            status_code=422,
            detail=_t("image_test_model_not_t2i_endpoint", model_id=body.model_id),
        )

    prompt = (body.prompt or "").strip() or _t("image_test_default_prompt")
    prompt = prompt[:_MAX_PROMPT_CHARS]

    result = await probe_image_generation(
        base_url=provider.base_url,
        api_key=provider.api_key,
        model=body.model_id,
        prompt=prompt,
    )
    return _serialize_image_probe(result, model_id=body.model_id, _t=_t)
```

> 检查：若 `model_not_found` i18n key 不存在，先在 zh/en/vi `errors.py` 各加一条 `"model_not_found": "..."`，三语保持一致；现有项目已有 `provider_not_found`，按相同格式追加即可。

- [ ] **Step 5.6: 跑端点测试确认 happy path PASS**

Run: `uv run pytest tests/test_custom_providers_api.py::TestImageGenerationEndpoint::test_happy_path_returns_image_data_url -v`
Expected: passed

- [ ] **Step 5.7: ruff + basedpyright**

Run:
```bash
uv run ruff check server/routers/custom_providers.py tests/test_custom_providers_api.py
uv run ruff format server/routers/custom_providers.py tests/test_custom_providers_api.py
uv run basedpyright server/routers/custom_providers.py
```
Expected: 0 errors

- [ ] **Step 5.8: Commit**

```bash
git add server/routers/custom_providers.py tests/test_custom_providers_api.py lib/i18n/
git commit -m "feat(api): POST /custom-providers/{id}/test-image-generation endpoint"
```

---

## Task 6: Backend 端点 — 错误路径测试

**Files:**
- Modify: `tests/test_custom_providers_api.py`

- [ ] **Step 6.1: 写错误路径测试**

Append to `TestImageGenerationEndpoint`:

```python
    @pytest.mark.asyncio
    async def test_provider_not_found_returns_404(self, async_client):
        resp = await async_client.post(
            "/api/v1/custom-providers/99999/test-image-generation",
            json={"model_id": "gpt-image-2"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_model_not_found_returns_404(self, async_client, custom_provider_with_image_model):
        provider = custom_provider_with_image_model
        resp = await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "nonexistent-model"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_t2i_endpoint_returns_422(
        self, async_client, custom_provider_with_image_model, db_session
    ):
        """openai-images-edits 是 I2I-only，应被拒绝。"""
        from lib.db.models.custom_provider import CustomProviderModel
        provider = custom_provider_with_image_model
        edits_only = CustomProviderModel(
            provider_id=provider.id,
            model_id="gpt-image-2-edits",
            display_name="GPT Image 2 Edits",
            endpoint="openai-images-edits",
            is_enabled=True,
        )
        db_session.add(edits_only)
        await db_session.commit()

        resp = await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "gpt-image-2-edits"},
        )
        assert resp.status_code == 422
        assert "text-to-image" in resp.json()["detail"].lower() or "文生图" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_default_prompt_used_when_not_provided(
        self, async_client, custom_provider_with_image_model, monkeypatch
    ):
        from lib.custom_provider import image_probe

        captured: dict = {}
        async def _capture_probe(**kw):
            captured.update(kw)
            return image_probe.ImageProbeResult(
                success=True, status_code=200, latency_ms=1, image_b64="x",
                mime_type="image/png", revised_prompt=None, error=None,
                upstream_model=None, upstream_size=None,
                upstream_quality=None, upstream_output_format=None,
            )
        monkeypatch.setattr("server.routers.custom_providers.probe_image_generation", _capture_probe)

        provider = custom_provider_with_image_model
        resp = await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "gpt-image-2"},  # 不传 prompt
        )
        assert resp.status_code == 200
        # 默认 prompt 应该被注入；具体文案见 i18n
        assert captured["prompt"]
        assert "cat astronaut" in captured["prompt"].lower()

    @pytest.mark.asyncio
    async def test_long_prompt_truncated_to_1000(
        self, async_client, custom_provider_with_image_model, monkeypatch
    ):
        from lib.custom_provider import image_probe

        captured: dict = {}
        async def _capture_probe(**kw):
            captured.update(kw)
            return image_probe.ImageProbeResult(
                success=True, status_code=200, latency_ms=1, image_b64="x",
                mime_type="image/png", revised_prompt=None, error=None,
                upstream_model=None, upstream_size=None,
                upstream_quality=None, upstream_output_format=None,
            )
        monkeypatch.setattr("server.routers.custom_providers.probe_image_generation", _capture_probe)

        provider = custom_provider_with_image_model
        long_prompt = "x" * 5000
        await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "gpt-image-2", "prompt": long_prompt},
        )
        assert len(captured["prompt"]) == 1000

    @pytest.mark.asyncio
    async def test_upstream_failure_returns_200_with_success_false(
        self, async_client, custom_provider_with_image_model, monkeypatch
    ):
        """上游 401 等业务失败：HTTP 仍 200, body.success=False."""
        from lib.custom_provider import image_probe

        async def _fake_probe(**_kw):
            return image_probe.ImageProbeResult(
                success=False, status_code=401, latency_ms=120,
                image_b64=None, mime_type="image/png",
                revised_prompt=None, error="invalid_api_key",
                upstream_model=None, upstream_size=None,
                upstream_quality=None, upstream_output_format=None,
            )
        monkeypatch.setattr("server.routers.custom_providers.probe_image_generation", _fake_probe)

        provider = custom_provider_with_image_model
        resp = await async_client.post(
            f"/api/v1/custom-providers/{provider.id}/test-image-generation",
            json={"model_id": "gpt-image-2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["status_code"] == 401
        assert body["image_data_url"] is None
        assert "invalid_api_key" in body["message"]
```

- [ ] **Step 6.2: 跑全部错误路径测试**

Run: `uv run pytest tests/test_custom_providers_api.py::TestImageGenerationEndpoint -v`
Expected: 6 passed (含 task 5 的 happy path 总共 6 个测试)

- [ ] **Step 6.3: ruff + basedpyright + pytest（全套）**

Run:
```bash
uv run ruff check tests/test_custom_providers_api.py
uv run pytest tests/test_custom_providers_api.py tests/test_custom_provider_image_probe.py tests/test_i18n_consistency.py -v
uv run basedpyright server/routers/custom_providers.py lib/custom_provider/image_probe.py
```
Expected: all pass, 0 type errors

- [ ] **Step 6.4: Commit**

```bash
git add tests/test_custom_providers_api.py
git commit -m "test(api): cover error paths for /test-image-generation endpoint"
```

---

## Task 7: 前端 API 方法 + i18n keys

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`
- Modify: `frontend/src/i18n/vi/dashboard.ts`

- [ ] **Step 7.1: 加 TypeScript 类型 + API 方法**

Edit `frontend/src/api.ts`，在 `testCustomConnectionById` 函数后插入：

```typescript
export interface UpstreamMetadata {
  model: string | null;
  size: string | null;
  quality: string | null;
  output_format: string | null;
}

export interface ImageGenerationTestResponse {
  success: boolean;
  message: string;
  latency_ms: number;
  status_code: number | null;
  image_data_url: string | null;
  revised_prompt: string | null;
  model: string;
  upstream_metadata: UpstreamMetadata;
}

// 加在 ApiClient class 内（与 testCustomConnectionById 同一缩进层）:
  static async testCustomProviderImageGeneration(
    id: number,
    data: { model_id: string; prompt?: string },
  ): Promise<ImageGenerationTestResponse> {
    return this.request(`/custom-providers/${id}/test-image-generation`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }
```

> 实施时若文件结构与上面假设不同（比如 ApiClient 名字不同 / interface 集中在别处 / 用 `export const fn` 而非 class static），按现有 pattern 调整。先 `grep -n "testCustomConnectionById" frontend/src/api.ts` 确认上下文。

- [ ] **Step 7.2: 加 zh 翻译 key**

Edit `frontend/src/i18n/zh/dashboard.ts`，在合适位置（grep 一下 `test_connection` 附近）加入：

```typescript
'image_test_button': '测试生图',
'image_test_modal_title': '测试生图能力',
'image_test_model_label': '模型',
'image_test_prompt_label': 'Prompt',
'image_test_default_prompt': 'A cute orange cat astronaut sticker on a clean pastel background.',
'image_test_start': '开始测试',
'image_test_loading': '生图通常需 10-30s，请耐心等待…',
'image_test_close': '关闭',
'image_test_retry': '再测一次',
'image_test_billing_warning': '⚠ 每次测试会真实计费（gpt-image-2 ~$0.04/张）',
'image_test_no_t2i_models': '此供应商下无支持文生图的模型，请先去模型列表配置一个',
'image_test_revised_prompt_label': '上游改写后的 prompt',
```

- [ ] **Step 7.3: 加 en 翻译 key**

Edit `frontend/src/i18n/en/dashboard.ts`，同位置加：

```typescript
'image_test_button': 'Test Image Generation',
'image_test_modal_title': 'Image Generation Test',
'image_test_model_label': 'Model',
'image_test_prompt_label': 'Prompt',
'image_test_default_prompt': 'A cute orange cat astronaut sticker on a clean pastel background.',
'image_test_start': 'Start Test',
'image_test_loading': 'Image generation usually takes 10-30s, please wait…',
'image_test_close': 'Close',
'image_test_retry': 'Retry',
'image_test_billing_warning': '⚠ Each test is real and billable (gpt-image-2 ~$0.04/image)',
'image_test_no_t2i_models': 'No text-to-image model configured for this provider. Add one first.',
'image_test_revised_prompt_label': 'Upstream revised prompt',
```

- [ ] **Step 7.4: 加 vi 翻译 key**

Edit `frontend/src/i18n/vi/dashboard.ts`，同位置加：

```typescript
'image_test_button': 'Kiểm tra tạo ảnh',
'image_test_modal_title': 'Kiểm tra khả năng tạo ảnh',
'image_test_model_label': 'Mô hình',
'image_test_prompt_label': 'Prompt',
'image_test_default_prompt': 'A cute orange cat astronaut sticker on a clean pastel background.',
'image_test_start': 'Bắt đầu kiểm tra',
'image_test_loading': 'Tạo ảnh thường mất 10-30s, vui lòng đợi…',
'image_test_close': 'Đóng',
'image_test_retry': 'Kiểm tra lại',
'image_test_billing_warning': '⚠ Mỗi lần kiểm tra đều tính phí thật (gpt-image-2 ~$0.04/ảnh)',
'image_test_no_t2i_models': 'Provider này chưa có mô hình text-to-image. Vui lòng thêm trước.',
'image_test_revised_prompt_label': 'Prompt sau khi upstream chỉnh sửa',
```

- [ ] **Step 7.5: 前端 lint + typecheck**

Run from `frontend/`:
```bash
cd frontend
pnpm lint
pnpm check
```
Expected: 0 errors, 0 warnings

- [ ] **Step 7.6: Commit**

```bash
git add frontend/src/api.ts frontend/src/i18n/
git commit -m "feat(frontend): api method + i18n keys for image generation test"
```

---

## Task 8: 前端 `ImageGenerationTestModal` 组件

**Files:**
- Create: `frontend/src/components/pages/settings/ImageGenerationTestModal.tsx`

- [ ] **Step 8.1: 写组件文件**

Create `frontend/src/components/pages/settings/ImageGenerationTestModal.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import { ApiClient, type ImageGenerationTestResponse } from "@/api";

const MAX_PROMPT_CHARS = 1000;

type ImageModel = {
  model_id: string;
  display_name: string;
  endpoint: string;  // 调用者已过滤为 T2I 端点
};

interface ImageGenerationTestModalProps {
  providerId: number;
  models: ImageModel[];  // 已被父组件过滤到 T2I 模型
  onClose: () => void;
}

export function ImageGenerationTestModal({
  providerId,
  models,
  onClose,
}: ImageGenerationTestModalProps) {
  const { t } = useTranslation("dashboard");

  // 默认选 model_id 含 "gpt-image" 的，否则第一个
  const initialModel = models.find((m) => m.model_id.toLowerCase().includes("gpt-image")) ?? models[0];
  const [modelId, setModelId] = useState<string>(initialModel?.model_id ?? "");
  const [prompt, setPrompt] = useState<string>(t("image_test_default_prompt"));
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ImageGenerationTestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Escape 键关闭
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [loading, onClose]);

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await ApiClient.testCustomProviderImageGeneration(providerId, {
        model_id: modelId,
        prompt: prompt.slice(0, MAX_PROMPT_CHARS),
      });
      setResult(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleRetry = () => {
    setResult(null);
    setError(null);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="image-test-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={() => !loading && onClose()}
    >
      <div
        className="relative w-full max-w-xl rounded-[12px] border border-hairline bg-bg-2 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          disabled={loading}
          className="absolute right-3 top-3 rounded p-1 text-text-3 hover:bg-bg-3 disabled:opacity-40"
          aria-label={t("image_test_close")}
        >
          <X className="h-4 w-4" />
        </button>

        <h2 id="image-test-modal-title" className="mb-4 text-[15px] font-semibold text-text-1">
          {t("image_test_modal_title")}
        </h2>

        {!result && !error && (
          <ImageTestForm
            models={models}
            modelId={modelId}
            setModelId={setModelId}
            prompt={prompt}
            setPrompt={setPrompt}
            loading={loading}
            onStart={handleStart}
            onCancel={onClose}
          />
        )}

        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-text-2">
            <Loader2 className="h-4 w-4 motion-safe:animate-spin" />
            <span>{t("image_test_loading")}</span>
          </div>
        )}

        {result && !loading && (
          <ImageTestResult result={result} onClose={onClose} onRetry={handleRetry} />
        )}

        {error && !loading && (
          <ImageTestError error={error} onClose={onClose} onRetry={handleRetry} />
        )}
      </div>
    </div>
  );
}

interface FormProps {
  models: ImageModel[];
  modelId: string;
  setModelId: (v: string) => void;
  prompt: string;
  setPrompt: (v: string) => void;
  loading: boolean;
  onStart: () => void;
  onCancel: () => void;
}

function ImageTestForm({ models, modelId, setModelId, prompt, setPrompt, loading, onStart, onCancel }: FormProps) {
  const { t } = useTranslation("dashboard");
  const overLimit = prompt.length > MAX_PROMPT_CHARS;

  return (
    <div className="space-y-4">
      <label className="block">
        <span className="mb-1 block text-[12px] text-text-2">{t("image_test_model_label")}</span>
        <select
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          disabled={loading}
          className="w-full rounded-md border border-hairline bg-bg-1 px-2 py-1.5 text-[13px]"
        >
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.display_name} ({m.model_id})
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="mb-1 block text-[12px] text-text-2">{t("image_test_prompt_label")}</span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          disabled={loading}
          rows={4}
          className="w-full rounded-md border border-hairline bg-bg-1 px-2 py-1.5 text-[13px]"
        />
        <div className={`mt-1 text-right text-[11px] ${overLimit ? "text-warm-bright" : "text-text-3"}`}>
          {prompt.length} / {MAX_PROMPT_CHARS}
        </div>
      </label>

      <div className="text-[11.5px] text-warm-bright">{t("image_test_billing_warning")}</div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          className="rounded-md border border-hairline px-3 py-1.5 text-[12.5px] text-text-2 hover:bg-bg-3"
        >
          {t("common:cancel")}
        </button>
        <button
          type="button"
          onClick={onStart}
          disabled={loading || !modelId}
          className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-semibold text-white disabled:opacity-50"
        >
          {t("image_test_start")}
        </button>
      </div>
    </div>
  );
}

function ImageTestResult({
  result,
  onClose,
  onRetry,
}: { result: ImageGenerationTestResponse; onClose: () => void; onRetry: () => void }) {
  const { t } = useTranslation("dashboard");

  if (!result.success) {
    return (
      <ImageTestError
        error={`${result.message} (HTTP ${result.status_code ?? "?"} · ${result.latency_ms}ms)`}
        onClose={onClose}
        onRetry={onRetry}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-[13px] font-medium text-emerald-400">
        ✓ {result.message} · {(result.latency_ms / 1000).toFixed(1)}s
      </div>
      {result.image_data_url && (
        <img
          src={result.image_data_url}
          alt="generated"
          className="mx-auto max-h-[400px] rounded-md border border-hairline"
        />
      )}
      {result.revised_prompt && (
        <details className="text-[12px] text-text-3">
          <summary className="cursor-pointer">{t("image_test_revised_prompt_label")}</summary>
          <p className="mt-1 whitespace-pre-wrap">{result.revised_prompt}</p>
        </details>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-hairline px-3 py-1.5 text-[12.5px] text-text-2 hover:bg-bg-3"
        >
          {t("image_test_retry")}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-semibold text-white"
        >
          {t("image_test_close")}
        </button>
      </div>
    </div>
  );
}

function ImageTestError({
  error,
  onClose,
  onRetry,
}: { error: string; onClose: () => void; onRetry: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-warm-ring bg-warm-tint px-3 py-2 text-[12.5px] text-warm-bright">
        ✗ {error}
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-hairline px-3 py-1.5 text-[12.5px] text-text-2 hover:bg-bg-3"
        >
          {t("image_test_retry")}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md bg-accent px-3 py-1.5 text-[12.5px] font-semibold text-white"
        >
          {t("image_test_close")}
        </button>
      </div>
    </div>
  );
}
```

> Tailwind class（如 `bg-bg-2`、`text-warm-bright` 等）来自项目自定义 design tokens，与 `CustomProviderDetail.tsx` 现有写法对齐。如果实施时发现 token 名不一致，按 `CustomProviderDetail.tsx:288-360` 行内代码中实际用的 token / inline style 同步。

- [ ] **Step 8.2: 前端 lint + typecheck**

```bash
cd frontend
pnpm lint
pnpm check
```
Expected: 0 errors

- [ ] **Step 8.3: Commit**

```bash
git add frontend/src/components/pages/settings/ImageGenerationTestModal.tsx
git commit -m "feat(frontend): ImageGenerationTestModal component"
```

---

## Task 9: 前端把按钮接入 `CustomProviderDetail`

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderDetail.tsx`

- [ ] **Step 9.1: 导入新组件**

Edit `frontend/src/components/pages/settings/CustomProviderDetail.tsx`，在顶部 import 区追加：

```typescript
import { ImageGenerationTestModal } from "./ImageGenerationTestModal";
```

- [ ] **Step 9.2: 添加 state + 派生 T2I 模型列表**

在组件内（已有 `const [testing, setTesting] = useState(false)` 附近）加：

```typescript
const [imageTestOpen, setImageTestOpen] = useState(false);

// T2I 端点白名单（与后端 spec.image_capabilities 含 TEXT_TO_IMAGE 的端点一致）
const T2I_ENDPOINTS = new Set(["openai-images", "openai-images-generations"]);
const t2iModels = (provider.models ?? []).filter(
  (m) => m.is_enabled && T2I_ENDPOINTS.has(m.endpoint),
);
```

> 实施时先 grep `provider.models` 确认字段名，必要时调整。

- [ ] **Step 9.3: 在 toolbar 加按钮**

找到 "测试连接" 按钮（约在 line 306-320），紧跟其后插入：

```tsx
<button
  type="button"
  onClick={() => setImageTestOpen(true)}
  disabled={testing || t2iModels.length === 0}
  title={t2iModels.length === 0 ? t("image_test_no_t2i_models") : undefined}
  className={GHOST_BTN_CLS}
>
  {t("image_test_button")}
</button>
```

- [ ] **Step 9.4: 在组件末尾 render Modal**

找到组件的 return 语句最外层 `</div>` 之前，加：

```tsx
{imageTestOpen && (
  <ImageGenerationTestModal
    providerId={provider.id}
    models={t2iModels.map((m) => ({
      model_id: m.model_id,
      display_name: m.display_name,
      endpoint: m.endpoint,
    }))}
    onClose={() => setImageTestOpen(false)}
  />
)}
```

- [ ] **Step 9.5: 前端 lint + typecheck + build**

```bash
cd frontend
pnpm lint
pnpm check
pnpm build
```
Expected: build 成功，无 lint/type 错误

- [ ] **Step 9.6: Commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderDetail.tsx
git commit -m "feat(frontend): wire image generation test button into provider detail"
```

---

## Task 10: 手工 e2e 联调（使用 spec 中实测过的账号）

**Files:** （无代码改动，只验证）

- [ ] **Step 10.1: 启动后端 dev server**

Run:
```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

- [ ] **Step 10.2: 启动前端 dev server**

新开 terminal:
```bash
cd frontend
pnpm dev
```

- [ ] **Step 10.3: 在 WebUI 配置一个自定义供应商**

浏览器打开 `http://localhost:5173/settings/custom-providers`，新建：
- display_name: `sub2api 测试`
- discovery_format: `openai`
- base_url: `https://proxy.hulupet.cn`
- api_key: `sk-d76e2fd9ae3eca832127f4f6258978b1ec30705fb05a2bed38a1c7545a79be2c`

保存后点「发现模型」，加 `gpt-image-2` 模型，endpoint 设为 `openai-images-generations`。

- [ ] **Step 10.4: 点「测试生图」按钮**

确认：
- 弹窗弹出，模型下拉里有 gpt-image-2
- prompt textarea 已预填默认 prompt
- 计费警告可见
- 点「开始测试」→ loading 状态显示 ≥60s
- 完成后图片正常预览，能看到橘猫宇航员贴纸
- Revised prompt 可折叠展开

- [ ] **Step 10.5: 验证错误路径**

- 临时把 api_key 改成无效值 → 重测 → 应展示 401 错误红框
- 把 prompt 清空 → 点「开始测试」→ 应使用默认 prompt 也能成功

- [ ] **Step 10.6: 截屏存档（可选）**

把成功+失败两种状态的截图存到 `/tmp/image_test_*.png`，作为本次实施的视觉证据。

- [ ] **Step 10.7: 关闭 dev server**

终端按 Ctrl+C 关闭前后端 server。

---

## Task 11: 收尾 — 全量校验 + 推送

**Files:**（无代码改动）

- [ ] **Step 11.1: 全量 lint / typecheck / 测试**

```bash
uv run ruff check . && uv run ruff format --check .
uv run basedpyright
uv run pytest tests/test_custom_provider_image_probe.py tests/test_custom_providers_api.py tests/test_i18n_consistency.py -v
cd frontend && pnpm lint && pnpm check && cd ..
```
Expected: 全部通过

- [ ] **Step 11.2: 总览本次 commits**

```bash
git log --oneline main..HEAD
```
Expected: ~10 个原子 commit

- [ ] **Step 11.3: 推送 + 开 PR**

```bash
git push -u origin <branch>
gh pr create --title "feat: 自定义供应商加 gpt-image-2 生图连通性测试" --body "$(cat <<'EOF'
## Summary
- 自定义供应商面板新增「测试生图」按钮，独立于现有「测试连接」（models.list）
- 后端 httpx 直调 `POST {base_url}/v1/images/generations`，与 lib/config/anthropic_probe.py 同 pattern
- 校验 model 必须含 TEXT_TO_IMAGE capability，拒绝 I2I-only 端点
- 前端弹窗预览返回的 base64 PNG + revised_prompt

灵感来源：Wei-Shaw/sub2api 的 testOpenAIImageAPIKey

设计文档：docs/superpowers/specs/2026-05-25-gpt-image-2-connection-test-design.md
实施计划：docs/superpowers/plans/2026-05-25-gpt-image-2-connection-test.md

## Test plan
- [x] `tests/test_custom_provider_image_probe.py` — 11 个用例覆盖 happy path / 401-5xx / timeout / 网络异常 / 200 empty data / 日志安全
- [x] `tests/test_custom_providers_api.py::TestImageGenerationEndpoint` — 6 个用例覆盖端点 happy/404/422/prompt 默认与裁断
- [x] `tests/test_i18n_consistency.py` — 三语 key 一致
- [x] 手工 e2e：用 sub2api 真号跑通生图，预览图正常
- [ ] code review

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Verification Checklist (Plan Self-Review)

实施完后回头逐项确认：

- [ ] spec 中所有 i18n key 都已加入三语 `errors.py` 并通过 consistency 测试
- [ ] timeout 120s（不是 60s）在 probe 默认值 + i18n 文案中一致
- [ ] 端点 URL = `/api/v1/custom-providers/{id}/test-image-generation`（非 `/test-image-gen` 等变体）
- [ ] 校验逻辑用 `ImageCapability.TEXT_TO_IMAGE in spec.image_capabilities`（拒绝 edits-only）
- [ ] `image_data_url` 形态 = `data:{mime};base64,{b64}`
- [ ] 日志测试断言不含 api_key 和 base64 字符
- [ ] 上游 401/429/5xx 业务失败时 HTTP 仍返 200，body.success=False
- [ ] 默认 prompt（i18n key `image_test_default_prompt`）三语统一英文
- [ ] OAuth `/responses` 路径未实现（spec 明确不做）
- [ ] 预置 OpenAI 供应商未改动（spec 明确不做）
