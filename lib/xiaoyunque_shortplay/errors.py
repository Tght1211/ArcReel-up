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
