import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///file:gb6101_test?mode=memory&cache=shared&uri=true")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from migrations.v001_workbench import upgrade as upgrade_v1
from migrations.v002_backfill import upgrade as upgrade_v2
from src.config import resources
from src.constants.roles import Role
from src.models.users import users
from src.utils.passwords import hash_password


class FakeCache:
    """测试用内存队列，替代 worker 依赖的 Redis。"""

    def __init__(self):
        self.queue = []

    def lpush(self, key, value):
        self.queue.append(value)

    def ltrim(self, key, start, end):
        self.queue = self.queue[start:end + 1]

    def blpop(self, key, timeout=0):
        return None

    def set(self, *args, **kwargs):
        return True

    def get(self, key):
        return None

    def ping(self):
        return True


@pytest.fixture(scope="session")
def engine():
    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    resources.engine = test_engine
    resources.cache = FakeCache()
    # 业务模块用 `from ...resources import engine` 绑定了引擎，逐一替换为测试引擎。
    import src.controllers.workbench as workbench
    import scripts.seed_demo as seed_demo
    import src.routes.backfills as backfill_routes
    import src.routes.cases as case_routes
    import src.routes.runs as run_routes
    import src.routes.system as system_routes
    import src.services.auth as auth_service
    import src.services.backfills as backfill_service
    import src.services.cases as case_service
    import src.services.execution as execution
    import src.services.reviews as review_service
    import src.services.runs as run_service
    for module in (workbench, seed_demo, backfill_routes, case_routes, run_routes, system_routes,
                   auth_service, backfill_service, case_service, execution,
                   review_service, run_service):
        module.engine = test_engine
    with test_engine.begin() as db:
        upgrade_v1(db)
        upgrade_v2(db)
        for role in Role:
            db.execute(text(
                "INSERT OR IGNORE INTO users (username, role, password_hash) VALUES (:u, :r, :h)"
            ), {"u": role.value, "r": role.value, "h": hash_password(os.environ["DEMO_PASSWORD"])})
    return test_engine


@pytest.fixture()
def db(engine):
    connection = engine.connect()
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def clean_business_tables(engine):
    yield
    with engine.begin() as connection:
        for table in ("receipt_backfills", "reviews", "runs", "cases", "audit_events"):
            connection.execute(text(f"DELETE FROM {table}"))


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app) as test_client:
        yield test_client


def login(client, username):
    response = client.post("/auth/login", json={
        "username": username, "password": os.environ["DEMO_PASSWORD"],
    })
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def auth(client):
    return {role: login(client, role) for role in ("operator", "reviewer", "viewer")}
