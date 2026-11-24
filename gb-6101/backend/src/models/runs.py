from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
    # 每补登一批回执并重算成功后加一；签核只记录签署当时看到的版本。
    Column("version", Integer, nullable=False, server_default="1"),
    # 重算轮次：补登使已完成批次重新入队时加一，失败续跑不重复计数。
    Column("revision", Integer, nullable=False, server_default="0"),
)
