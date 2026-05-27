"""Volcengine Visual Service Sig v4 签名工具。

仅依赖 hashlib + hmac，无新外部依赖。

参考：火山官方文档 - 公共参数 - 签名参数。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote

VISUAL_HOST = "visual.volcengineapi.com"
DEFAULT_REGION = "cn-north-1"
DEFAULT_SERVICE = "cv"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_query(query: dict[str, str]) -> str:
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query.items()))


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
    ts = (timestamp or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
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

    def _value_for(lower_name: str) -> str:
        for k, v in out_headers.items():
            if k.lower() == lower_name:
                return v.strip()
        return ""

    canonical_headers = "".join(f"{name}:{_value_for(name)}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)
    canonical_request = (
        f"{method.upper()}\n{path}\n{_canonical_query(query)}\n{canonical_headers}\n{signed_headers}\n{body_sha}"
    )

    # 2. string to sign
    credential_scope = f"{date}/{region}/{service}/request"
    string_to_sign = f"HMAC-SHA256\n{ts}\n{credential_scope}\n{_hex_sha256(canonical_request.encode())}"

    # 3. signing key
    k_date = _hmac_sha256(secret_key.encode("utf-8"), date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out_headers["Authorization"] = (
        f"HMAC-SHA256 Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    return out_headers
