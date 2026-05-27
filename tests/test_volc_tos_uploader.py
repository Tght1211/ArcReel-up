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
        ak="ak",
        sk="sk",
        endpoint="tos-cn-beijing.volces.com",
        bucket="my-bucket",
        region="cn-beijing",
    )

    fake_resp = MagicMock(status_code=200)
    fake_resp.raise_for_status = MagicMock()

    # httpx.AsyncClient().put 是 instance method，需要 patch 类层级
    with patch("httpx.AsyncClient.put", new=AsyncMock(return_value=fake_resp)):
        url = await uploader.upload_image(fake_image)

    assert url.startswith("https://my-bucket.tos-cn-beijing.volces.com/arcreel-ref/")
    assert url.endswith(".png") or ".png?" in url  # 可能 ext 在 query 之前
    assert "X-Tos-Signature=" in url
    assert "X-Tos-Date=" in url
    assert "X-Tos-Expires=" in url


@pytest.mark.asyncio
async def test_upload_uses_content_hash_in_key(fake_image):
    uploader = TosImageUploader(
        ak="ak",
        sk="sk",
        endpoint="tos-cn-beijing.volces.com",
        bucket="b",
        region="cn-beijing",
    )

    fake_resp = MagicMock(status_code=200)
    fake_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.put", new=AsyncMock(return_value=fake_resp)):
        u1 = await uploader.upload_image(fake_image)
        u2 = await uploader.upload_image(fake_image)

    # 同内容应有相同 key（query 部分可能因 timestamp 不同，但 path 必须相同）
    key1 = u1.split("?", 1)[0]
    key2 = u2.split("?", 1)[0]
    assert key1 == key2
