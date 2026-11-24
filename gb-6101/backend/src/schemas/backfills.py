from pydantic import BaseModel, Field, field_validator


class ReceiptInput(BaseModel):
    receipt_key: str = Field(min_length=1, max_length=128)
    asset: str = Field(min_length=1, max_length=64)
    check: str = Field(min_length=1, max_length=64)
    checked: int = Field(strict=True, ge=0, le=1440)
    expires: int = Field(strict=True, ge=1, le=1440)
    status: str

    @field_validator("receipt_key", "asset", "check")
    @classmethod
    def strip_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("标识不能为空")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value):
        if value not in ("pass", "fail"):
            raise ValueError("回执结论仅接受 pass 或 fail")
        return value


class BackfillInput(BaseModel):
    receipts: list[ReceiptInput] = Field(min_length=1, max_length=200)


class BackfillReceiptEntry(BaseModel):
    id: int | None = None
    receipt_key: str
    seq: int
    asset: str
    check: str
    checked: int
    expires: int
    status: str
    actor: str | None = None


class BackfillResult(BaseModel):
    case_id: int
    accepted: list[BackfillReceiptEntry]
    duplicate: list[str]
    affected_run_ids: list[int]
    recomputing: bool
