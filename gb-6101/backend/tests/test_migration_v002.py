"""v002 迁移专项：在仅含 v1 结构的旧库上补列、建表，并保证可重复执行。"""

from sqlalchemy import (
    JSON, BigInteger, Column, ForeignKey, Integer, MetaData, String, Table, create_engine, inspect,
)
from sqlalchemy.pool import StaticPool

from migrations.v002_backfill import upgrade
from src.models.base import metadata as current_metadata


def _legacy_schema():
    legacy = MetaData()
    Table("cases", legacy,
          Column("id", Integer, primary_key=True, autoincrement=True),
          Column("code", String(48), unique=True, nullable=False),
          Column("title", String(120), nullable=False),
          Column("description", String(420), nullable=False),
          Column("payload", JSON, nullable=False),
          Column("created_by", String(48), nullable=False))
    Table("runs", legacy,
          Column("id", Integer, primary_key=True, autoincrement=True),
          Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
          Column("status", String(16), nullable=False, index=True),
          Column("parameter", Integer, nullable=False),
          Column("actor", String(48), nullable=False),
          Column("created_at", BigInteger, nullable=False),
          Column("finished_at", BigInteger, nullable=True),
          Column("result", JSON, nullable=True),
          Column("failure", String(320), nullable=True),
          Column("digest", String(64), nullable=True))
    Table("reviews", legacy,
          Column("run_id", ForeignKey("runs.id"), primary_key=True),
          Column("actor", String(48), nullable=False),
          Column("note", String(500), nullable=False),
          Column("created_at", BigInteger, nullable=False))
    return legacy


def test_upgrade_legacy_database_adds_columns_with_defaults_and_backfill_table():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    legacy = _legacy_schema()
    with engine.begin() as db:
        legacy.create_all(db)
        db.execute(legacy.tables["cases"].insert().values(
            code="CASE-LEGACY", title="旧库资料", description="v1", payload={}, created_by="seed"))
        db.execute(legacy.tables["runs"].insert().values(
            case_id=1, status="done", parameter=480, actor="operator", created_at=1, result={}, digest="abc"))
        db.execute(legacy.tables["reviews"].insert().values(
            run_id=1, actor="reviewer", note="旧版签署", created_at=2))

    with engine.begin() as db:
        upgrade(db)
        run = db.exec_driver_sql("SELECT version, revision FROM runs WHERE id = 1").fetchone()
        assert tuple(run) == (1, 0)
        review = db.exec_driver_sql(
            "SELECT result_version, result_digest FROM reviews WHERE run_id = 1"
        ).fetchone()
        assert tuple(review) == (1, "")
        assert "receipt_backfills" in inspect(db).get_table_names()

    # 幂等：再次执行不报错，当前模型结构可在其上正常校验。
    with engine.begin() as db:
        upgrade(db)
        current_metadata.tables["receipt_backfills"].create(db, checkfirst=True)
        columns = {item["name"] for item in inspect(db).get_columns("runs")}
        assert {"version", "revision"}.issubset(columns)
        review_columns = {item["name"] for item in inspect(db).get_columns("reviews")}
        assert {"result_version", "result_digest"}.issubset(review_columns)
