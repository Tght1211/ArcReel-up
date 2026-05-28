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
