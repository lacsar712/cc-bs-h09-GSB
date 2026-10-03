"""H09 验收：空跨段必须在落盘前拦截，禁止代名/半截脏行；合法读数正常入队。

不依赖真实 PostgreSQL：用 FakePool 记录所有 execute/commit，
精确断言拒绝路径“零触库”、合法路径“只插一条且参数干净”。
"""

import asyncio
import datetime
import importlib

import httpx
import jwt
import pytest

from api import app as app_module


VALID_ROW = {
    "id": 1,
    "span_code": "跨中S3",
    "microstrain": 150.0,
    "verdict": None,
    "reason": None,
    "status": "pending",
    "created_by": "surveyor",
    "created_at": datetime.datetime(2026, 10, 3, tzinfo=datetime.timezone.utc),
    "processed_at": None,
}


class FakeCursor:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.pool.executed.append((sql, params))

    async def fetchone(self):
        return dict(VALID_ROW)


class FakeConnection:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self.pool)

    async def commit(self):
        self.pool.commits += 1


class FakePool:
    def __init__(self):
        self.executed = []
        self.commits = 0

    def connection(self):
        return FakeConnection(self)

    async def close(self):
        pass


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def pool():
    return FakePool()


@pytest.fixture(autouse=True)
def _reset_pool(pool):
    pool.executed.clear()
    pool.commits = 0


@pytest.fixture(scope="session")
async def client(pool):
    # Sanic app 是全局单例，lifespan 全会话只启停一次。
    originals = {
        "create_pool": app_module.create_pool,
        "ensure_schema": app_module.ensure_schema,
        "seed_if_empty": app_module.seed_if_empty,
    }

    async def fake_create_pool():
        return pool

    async def noop(_pool):
        pass

    app_module.create_pool = fake_create_pool
    app_module.ensure_schema = noop
    app_module.seed_if_empty = noop

    from asgi_lifespan import LifespanManager

    transport = httpx.ASGITransport(app=app_module.app)
    async with LifespanManager(app_module.app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://bridge-strain-test"
        ) as c:
            yield c

    app_module.create_pool = originals["create_pool"]
    app_module.ensure_schema = originals["ensure_schema"]
    app_module.seed_if_empty = originals["seed_if_empty"]


def token(sub, role):
    return jwt.encode(
        {"sub": sub, "role": role, "exp": 9_999_999_999},
        app_module.SECRET,
        algorithm="HS256",
    )


WRITER_HEADERS = {"Authorization": f"Bearer {token('surveyor', 'writer')}"}
READER_HEADERS = {"Authorization": f"Bearer {token('reviewer', 'reader')}"}


@pytest.mark.parametrize("span", ["", "   ", "\t", "　"])
async def test_blank_span_rejected_before_persist(client, pool, span):
    """空串/纯空格（含制表符、全角空格）→ 400，且完全不触库。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": span, "microstrain": 150},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 400
    assert "跨段编号" in resp.json()["detail"]
    assert pool.executed == []
    assert pool.commits == 0


@pytest.mark.parametrize("span", [None, 123, [], {}])
async def test_non_string_span_rejected(client, pool, span):
    """None / 数字 / 数组 / 对象都不许被 str() 吞掉后写库。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": span, "microstrain": 150},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 400
    assert pool.executed == []


async def test_missing_span_key_rejected(client, pool):
    resp = await client.post(
        "/api/readings", json={"microstrain": 150}, headers=WRITER_HEADERS
    )
    assert resp.status_code == 400
    assert pool.executed == []


async def test_double_click_blank_leaves_no_dirty_rows(client, pool):
    """连点两次空跨段：两次都 400，条目数不得悄悄变多，不许有半截脏行。"""
    for _ in range(2):
        resp = await client.post(
            "/api/readings",
            json={"span_code": "  ", "microstrain": 150},
            headers=WRITER_HEADERS,
        )
        assert resp.status_code == 400
    assert pool.executed == []
    assert pool.commits == 0


async def test_valid_reading_enqueued_once(client, pool):
    """合法跨段 + 够线微应变 → 201，全表只执行一条 INSERT，参数即用户输入。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": "跨中S3", "microstrain": 150},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["span_code"] == "跨中S3"
    assert data["status"] == "pending"
    assert data["created_by"] == "surveyor"

    inserts = [e for e in pool.executed if "insert into strain_readings" in e[0].lower()]
    assert len(inserts) == 1
    assert inserts[0][1] == ("跨中S3", 150.0, "surveyor")
    assert pool.commits == 1


async def test_valid_span_is_trimmed_not_renamed(client, pool):
    """首尾空格只做 trim，绝不能替换成代名。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": "  跨中S3  ", "microstrain": 150},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 201
    assert pool.executed[-1][1][0] == "跨中S3"
    for _sql, params in pool.executed:
        joined = " ".join(str(p) for p in (params or ()))
        assert "代起跨段" not in joined
        assert joined.split(",")[0] != ""


async def test_writer_valid_enqueue_uses_single_statement(client, pool):
    """合法路径只有一条 INSERT，不得夹带预插/更新/删除（种子不被牵连）。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": "支座S9", "microstrain": 200},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 201
    assert len(pool.executed) == 1
    sql = pool.executed[0][0].lower()
    assert "insert into strain_readings" in sql
    assert "update" not in sql and "delete" not in sql


async def test_reviewer_cannot_write(client, pool):
    """复核身份只读：403，且不触库。"""
    resp = await client.post(
        "/api/readings",
        json={"span_code": "跨中S3", "microstrain": 150},
        headers=READER_HEADERS,
    )
    assert resp.status_code == 403
    assert pool.executed == []


async def test_invalid_microstrain_with_valid_span_rejected(client, pool):
    resp = await client.post(
        "/api/readings",
        json={"span_code": "跨中S3", "microstrain": "abc"},
        headers=WRITER_HEADERS,
    )
    assert resp.status_code == 400
    assert pool.executed == []


async def test_anonymous_rejected(client, pool):
    resp = await client.post(
        "/api/readings", json={"span_code": "跨中S3", "microstrain": 150}
    )
    assert resp.status_code == 401
    assert pool.executed == []


def test_trap_modules_removed():
    """自动代名/预插脏行的陷阱模块必须彻底移除。"""
    for name in ("blank_span", "h09_pad_trap", "h09_extra_trap"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
