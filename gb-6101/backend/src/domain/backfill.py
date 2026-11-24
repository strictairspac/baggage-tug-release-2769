"""漏登检证回执的补登口径。

同一张回执按幂等键去重，重复提交不重复入账；补登后与原证据合并，
仍由 release_sql 的 freshness 排序（检测分钟、证据序号降序）决定哪条生效，
因此晚到的较早检测记录不会顶掉已经生效的较新回执。
"""

from .inputs import _key, _table, validate

RECEIPT_FIELDS = ("receipt_key", "asset", "check", "checked", "expires", "status")


def receipt_key(receipt):
    """回执幂等键：同一张纸质/仪器回执重试补登时保持一致。"""
    return str(receipt["receipt_key"]).strip()


def validate_receipt(receipt, payload):
    if not isinstance(receipt, dict) or set(receipt) != set(RECEIPT_FIELDS):
        raise ValueError(f"回执需要字段 {', '.join(RECEIPT_FIELDS)}")
    key = _key(receipt, "receipt_key")
    if len(key) > 128:
        raise ValueError("receipt_key 须为 1–128 字符的回执标识")
    # 复用证据行的全部业务校验，补登回执必须能挂到本资料的车辆与必检规则上。
    validate({
        "vehicles": payload["vehicles"],
        "requirements": payload["requirements"],
        "evidence": [{
            "seq": 1, "asset": receipt["asset"], "check": receipt["check"],
            "checked": receipt["checked"], "expires": receipt["expires"], "status": receipt["status"],
        }],
    })
    return key


def validate_batch(payload, receipts):
    validate(payload)
    rows = _table({"receipts": receipts}, "receipts", RECEIPT_FIELDS, allow_empty=False)
    keys = set()
    for receipt in rows:
        key = validate_receipt(receipt, payload)
        if key in keys:
            raise ValueError(f"同一批补登中回执重复 {key}")
        keys.add(key)
    return rows


def merge_receipts(payload, receipts):
    """把补登回执并入资料证据；与既有证据重复的回执直接丢弃，不重复入账。"""
    prior = {receipt_key(item): item for item in payload.get("backfill_receipts", [])}
    # 原始证据保持不动；补登证据按台账重建，避免与已并入的行重复。
    evidence = [dict(row) for row in payload["evidence"]
                if not any(row["seq"] == item["seq"] for item in prior.values())]
    occupied_seq = {row["seq"] for row in evidence} | {item["seq"] for item in prior.values()}
    next_seq = max(occupied_seq, default=0) + 1
    accepted, duplicate = [], []
    for receipt in receipts:
        key = receipt_key(receipt)
        if key in prior:
            duplicate.append(key)
            continue
        stored = {"receipt_key": key, "seq": next_seq, "asset": receipt["asset"], "check": receipt["check"],
                  "checked": receipt["checked"], "expires": receipt["expires"], "status": receipt["status"]}
        next_seq += 1
        prior[key] = stored
        accepted.append(stored)
    merged_backfills = sorted(prior.values(), key=lambda item: item["seq"])
    merged = {
        "vehicles": payload["vehicles"],
        "requirements": payload["requirements"],
        "evidence": evidence + [{k: item[k] for k in ("seq", "asset", "check", "checked", "expires", "status")}
                                for item in merged_backfills],
        "backfill_receipts": merged_backfills,
    }
    validate(merged)
    return merged, accepted, duplicate
