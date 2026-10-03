"""H09：空跨段（空串或纯空白）必须在落盘之前被拦截。

- 不自动代起名、不写半截脏行：拦截路径不得触碰数据库连接池
- 连点两次空跨段、校验出错，都不得产生任何条目
- 复核员（reader）一律不可写
"""

import asyncio
import json
import types

import jwt

from api.app import SECRET, create_reading
from blank_span import is_blankish, normalize_span


def _token(username="surveyor", role="writer"):
    return jwt.encode({"sub": username, "role": role}, SECRET, algorithm="HS256")


class _NoPool:
    """拦截路径一旦试图拿数据库连接就立即失败。"""

    def connection(self):
        raise AssertionError("拦截必须发生在落盘之前，不得触碰数据库")


class _FakeRequest:
    def __init__(self, payload, token):
        self.json = payload
        self.headers = {"Authorization": f"Bearer {token}"}
        self.app = types.SimpleNamespace(ctx=types.SimpleNamespace(pool=_NoPool()))


def _post(payload, token=None):
    req = _FakeRequest(payload, token or _token())
    return asyncio.run(create_reading(req))


def test_normalize_span_strips_and_never_autofills():
    assert normalize_span(" 跨中S3 ") == "跨中S3"
    assert normalize_span("") == ""
    assert normalize_span("   ") == ""
    assert normalize_span(None) == ""
    assert normalize_span("  ") == ""  # 空跨段保持空，由调用方拒绝，绝不代起名


def test_is_blankish():
    for blank in ("", "   ", "\t\n ", None):
        assert is_blankish(blank) is True
    assert is_blankish("跨中S3") is False


def test_blank_span_rejected_before_db():
    for blank in ("", "   ", "\t\n ", None):
        res = _post({"span_code": blank, "microstrain": 150})
        assert res.status == 400
        assert json.loads(res.body)["detail"] == "跨段编号不能为空"


def test_missing_span_key_rejected_before_db():
    res = _post({"microstrain": 150})
    assert res.status == 400


def test_double_click_blank_span_stays_clean():
    # 连点两次空跨段：两次都 400，且全程未触碰连接池，条目数不变
    for _ in range(2):
        res = _post({"span_code": "  ", "microstrain": 150})
        assert res.status == 400


def test_bad_microstrain_rejected_before_db():
    # 校验出错同样不得留下半截脏行
    res = _post({"span_code": "跨中S3", "microstrain": "abc"})
    assert res.status == 400
    assert json.loads(res.body)["detail"] == "微应变必须是数字"


def test_reviewer_cannot_write():
    res = _post(
        {"span_code": "跨中S3", "microstrain": 150},
        token=_token("reviewer", "reader"),
    )
    assert res.status == 403
