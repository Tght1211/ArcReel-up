"""Volcengine Visual Service Sig v4 — 关键不变量测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from lib.volc_visual_shared import sign_request


def test_sign_request_produces_required_headers():
    headers = sign_request(
        method="POST",
        host="visual.volcengineapi.com",
        path="/",
        query={"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"},
        headers={"Content-Type": "application/json"},
        body=b'{"req_key":"x"}',
        access_key="AKLTtest",
        secret_key="c2VjcmV0X2tleV9mb3JfdGVzdA==",
        timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
    )
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("HMAC-SHA256 Credential=AKLTtest/20260528/cn-north-1/cv/request,")
    assert headers["X-Date"] == "20260528T120000Z"
    assert headers["Host"] == "visual.volcengineapi.com"
    assert "Content-Type" in headers
    # X-Content-Sha256 必须等于 body 的 hex sha256
    import hashlib

    assert headers["X-Content-Sha256"] == hashlib.sha256(b'{"req_key":"x"}').hexdigest()


def test_sign_request_canonicalizes_query_lexicographically():
    h1 = sign_request(
        method="POST",
        host="visual.volcengineapi.com",
        path="/",
        query={"Version": "v", "Action": "a"},
        headers={},
        body=b"",
        access_key="ak",
        secret_key="sk",
        timestamp=datetime(2026, 5, 28, tzinfo=UTC),
    )
    h2 = sign_request(
        method="POST",
        host="visual.volcengineapi.com",
        path="/",
        query={"Action": "a", "Version": "v"},  # 倒序
        headers={},
        body=b"",
        access_key="ak",
        secret_key="sk",
        timestamp=datetime(2026, 5, 28, tzinfo=UTC),
    )
    assert h1["Authorization"] == h2["Authorization"]


def test_signing_key_includes_all_scope_segments():
    """同 AK/SK 不同 region 应得不同签名。"""
    base = dict(
        method="POST",
        host="visual.volcengineapi.com",
        path="/",
        query={"Action": "a", "Version": "v"},
        headers={},
        body=b"",
        access_key="ak",
        secret_key="sk",
        timestamp=datetime(2026, 5, 28, tzinfo=UTC),
    )
    h_default = sign_request(**base)
    h_other = sign_request(**{**base, "region": "ap-southeast-1"})
    assert h_default["Authorization"] != h_other["Authorization"]
