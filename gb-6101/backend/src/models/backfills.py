from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table, UniqueConstraint
from .base import metadata


# 换班前漏登的牵引车检证回执，按回执幂等键去重补登；同键重试不重复入账。
backfills = Table(
    "receipt_backfills", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("receipt_key", String(160), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("case_id", "receipt_key", name="uq_backfill_case_receipt"),
)
