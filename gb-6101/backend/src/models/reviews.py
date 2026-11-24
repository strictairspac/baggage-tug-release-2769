from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


reviews = Table(
    "reviews", metadata,
    Column("run_id", ForeignKey("runs.id"), primary_key=True),
    Column("actor", String(48), nullable=False),
    Column("note", String(500), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    # 签署落版：记录复核员看到且仍有效的结果版本与摘要，之后补登不再改动已签核记录。
    Column("result_version", Integer, nullable=False, server_default="1"),
    Column("result_digest", String(64), nullable=False, server_default=""),
)
