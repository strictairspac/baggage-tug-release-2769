import pytest

from src.domain import backfill
from tests.helpers import REGULAR_PAYLOAD


def receipt(key, seq_asset="YP-D22", check="制动", checked=470, expires=600, status="pass"):
    return {"receipt_key": key, "asset": seq_asset, "check": check,
            "checked": checked, "expires": expires, "status": status}


def test_merged_payload_appends_backfill_with_new_seq_and_keeps_originals():
    merged, accepted, duplicate = backfill.merge_receipts(
        REGULAR_PAYLOAD, [receipt("RC-1", checked=470)]
    )
    assert duplicate == []
    assert len(accepted) == 1 and accepted[0]["seq"] == 7
    original_seqs = {item["seq"] for item in REGULAR_PAYLOAD["evidence"]}
    assert original_seqs.issubset({item["seq"] for item in merged["evidence"]})
    # 标准证据表不携带回执键，补登台账单独保存。
    assert set(merged["evidence"][-1]) == {"seq", "asset", "check", "checked", "expires", "status"}
    assert merged["backfill_receipts"][0]["receipt_key"] == "RC-1"


def test_same_receipt_retry_is_idempotent_and_never_double_posted():
    merged, accepted, duplicate = backfill.merge_receipts(REGULAR_PAYLOAD, [receipt("RC-1")])
    assert len(accepted) == 1
    again, accepted2, duplicate2 = backfill.merge_receipts(merged, [receipt("RC-1")])
    assert accepted2 == []
    assert duplicate2 == ["RC-1"]
    # 证据集与台账都没有增加。
    assert again["evidence"] == merged["evidence"]
    assert again["backfill_receipts"] == merged["backfill_receipts"]


def test_older_late_receipt_ranks_below_newer_effective_one():
    # YP-D22 制动已有 checked=410 的 pass（480 时点已失效）；补一张更早的 400 分回执。
    older = receipt("RC-OLD", checked=400, expires=600)
    merged, _, _ = backfill.merge_receipts(REGULAR_PAYLOAD, [older])
    d22 = sorted(
        (item for item in merged["evidence"] if item["asset"] == "YP-D22"),
        key=lambda item: (item["checked"], item["seq"]), reverse=True,
    )
    # 晚到的较早记录没有顶掉较新回执的排名，补登行排在其后。
    assert [item["checked"] for item in d22] == [410, 400]
    assert d22[1]["seq"] == 7


def test_newer_backfill_can_flip_decision_but_older_cannot():
    from tests.helpers import python_release_decisions

    # 较新且有效的补登让 YP-D22 在 480 时点放行。
    merged_good, _, _ = backfill.merge_receipts(REGULAR_PAYLOAD, [receipt("RC-NEW", checked=470, expires=600)])
    decisions = {item["asset"]: item for item in python_release_decisions(merged_good, 480)}
    assert decisions["YP-D22"]["released"] is True

    # 较早（400 分）的补登排在 410 分回执之后，410 分回执 480 已失效，仍停放。
    merged_old, _, _ = backfill.merge_receipts(REGULAR_PAYLOAD, [receipt("RC-OLD", checked=400, expires=600)])
    decisions = {item["asset"]: item for item in python_release_decisions(merged_old, 480)}
    assert decisions["YP-D22"]["released"] is False
    assert "证据已失效" in decisions["YP-D22"]["diagnostic"]


def test_duplicate_keys_within_one_batch_are_rejected():
    with pytest.raises(ValueError, match="回执重复"):
        backfill.validate_batch(REGULAR_PAYLOAD, [receipt("RC-X"), receipt("RC-X")])


def test_receipt_must_match_vehicle_and_requirement():
    with pytest.raises(ValueError):
        backfill.validate_batch(REGULAR_PAYLOAD, [receipt("RC-X", seq_asset="YP-GHOST")])
    with pytest.raises(ValueError):
        backfill.validate_batch(REGULAR_PAYLOAD, [receipt("RC-X", check="灯光")])


def test_fail_status_and_time_window_validated():
    with pytest.raises(ValueError):
        backfill.validate_batch(REGULAR_PAYLOAD, [receipt("RC-X", status="unknown")])
    with pytest.raises(ValueError):
        backfill.validate_batch(REGULAR_PAYLOAD, [receipt("RC-X", checked=500, expires=400)])
