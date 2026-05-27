"""把本地图片 PUT 到火山 TOS，返回预签名 GET URL（默认 TTL 3600s）。

走 TOS 的 S3 兼容 sigv4 手实现，不引入 tos SDK 依赖。
TOS 算法名 = TOS4-HMAC-SHA256，前缀 X-Tos-*。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from lib.retry import with_retry_async


def _hex_sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _content_type_for(ext: str) -> str:
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


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
        self,
        local_path: Path,
        *,
        key_prefix: str = "arcreel-ref/",
        ttl_seconds: int = 3600,
    ) -> str:
        body = local_path.read_bytes()
        sha = hashlib.sha256(body).hexdigest()[:16]
        ext = local_path.suffix.lower() or ".png"
        key = f"{key_prefix}{sha}{ext}"

        now = datetime.now(UTC)
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
            key=key,
            headers=put_headers,
            body_hash=body_hash,
            ts=ts,
            date=date,
        )

        put_url = f"https://{self.host}/{quote(key, safe='/')}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(put_url, content=body, headers=put_headers)
            resp.raise_for_status()

        return self._presign_get(key=key, ttl_seconds=ttl_seconds, now=now)

    def _sign_put(
        self,
        *,
        key: str,
        headers: dict[str, str],
        body_hash: str,
        ts: str,
        date: str,
    ) -> str:
        signed_names = sorted(k.lower() for k in headers)

        def _value_for(lower_name: str) -> str:
            for k, v in headers.items():
                if k.lower() == lower_name:
                    return v.strip()
            return ""

        canonical_headers = "".join(f"{name}:{_value_for(name)}\n" for name in signed_names)
        signed_headers = ";".join(signed_names)
        canonical_request = f"PUT\n/{quote(key, safe='/')}\n\n{canonical_headers}\n{signed_headers}\n{body_hash}"
        scope = f"{date}/{self.region}/{self.service}/request"
        sts = f"TOS4-HMAC-SHA256\n{ts}\n{scope}\n{_hex_sha256(canonical_request.encode())}"
        k = _hmac(self.sk.encode("utf-8"), date)
        k = _hmac(k, self.region)
        k = _hmac(k, self.service)
        k = _hmac(k, "request")
        sig = hmac.new(k, sts.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"TOS4-HMAC-SHA256 Credential={self.ak}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"

    def _presign_get(self, *, key: str, ttl_seconds: int, now: datetime) -> str:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        date = ts[:8]
        scope = f"{date}/{self.region}/{self.service}/request"
        credential = f"{self.ak}/{scope}"
        query: dict[str, str] = {
            "X-Tos-Algorithm": "TOS4-HMAC-SHA256",
            "X-Tos-Credential": credential,
            "X-Tos-Date": ts,
            "X-Tos-Expires": str(ttl_seconds),
            "X-Tos-SignedHeaders": "host",
        }
        canonical_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query.items()))
        canonical_headers = f"host:{self.host}\n"
        canonical_request = (
            f"GET\n/{quote(key, safe='/')}\n{canonical_query}\n{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"
        )
        sts = f"TOS4-HMAC-SHA256\n{ts}\n{scope}\n{_hex_sha256(canonical_request.encode())}"
        k = _hmac(self.sk.encode("utf-8"), date)
        k = _hmac(k, self.region)
        k = _hmac(k, self.service)
        k = _hmac(k, "request")
        sig = hmac.new(k, sts.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"https://{self.host}/{quote(key, safe='/')}?{canonical_query}&X-Tos-Signature={sig}"
