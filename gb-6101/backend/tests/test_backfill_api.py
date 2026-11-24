import pytest

from src.constants.job_state import JobState
from src.services.execution import JobFailure, fail, service_one
from tests.helpers import REGULAR_PAYLOAD, make_case, patched_scenario, run_to_done


def d22_brake(receipt_key, **overrides):
    base = {"receipt_key": receipt_key, "asset": "YP-D22", "check": "制动",
            "checked": 470, "expires": 600, "status": "pass"}
    base.update(overrides)
    return base


def test_backfill_recomputes_unsigned_run_and_reopens_handover(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)
    assert run["version"] == 1
    decisions = {row["车辆"]: row for row in run["result"]["rows"]}
    assert decisions["YP-D22"]["放行决定"] == "停放"

    outcome = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                          json={"receipts": [d22_brake("RC-100")]}).json()
    assert outcome["accepted"][0]["receipt_key"] == "RC-100"
    assert outcome["affected_run_ids"] == [run["id"]]

    held = client.get(f"/runs/{run['id']}", headers=auth["reviewer"]).json()
    assert held["status"] == JobState.QUEUED
    # 暂停期间签核与下载都被拒。
    assert client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                       json={"note": "暂停中不应签署"}).status_code == 409
    assert client.get(f"/runs/{run['id']}/export", headers=auth["viewer"]).status_code == 409

    assert service_one() is True
    done = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert done["status"] == JobState.DONE and done["version"] == 2
    decisions = {row["车辆"]: row for row in done["result"]["rows"]}
    assert decisions["YP-D22"]["放行决定"] == "放行"
    # 算完恢复交接：可以签核和下载。
    review = client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                         json={"note": "重算后版本核对无误"})
    assert review.status_code == 201
    assert review.json()["result_version"] == 2
    assert client.get(f"/runs/{run['id']}/export", headers=auth["viewer"]).status_code == 200


def test_same_receipt_retry_does_not_double_post_or_requeue(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)

    first = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                        json={"receipts": [d22_brake("RC-DUP")]})
    assert first.status_code == 202 and len(first.json()["accepted"]) == 1
    again = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                        json={"receipts": [d22_brake("RC-DUP")]})
    assert again.status_code == 202
    body = again.json()
    assert body["accepted"] == [] and body["duplicate"] == ["RC-DUP"]
    assert body["recomputing"] is False and body["affected_run_ids"] == []

    ledger = client.get(f"/cases/{case_id}/backfills", headers=auth["viewer"]).json()
    assert len(ledger) == 1
    # 重复补登没有再次触发重算：状态停在首次补登造成的排队，版本未升。
    detail = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert detail["status"] == JobState.QUEUED and detail["version"] == 1
    assert service_one() is True
    done = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert done["status"] == JobState.DONE and done["version"] == 2


def test_late_older_receipt_keeps_newer_effective_one(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)

    response = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"], json={
        "receipts": [d22_brake("RC-OLD", checked=400, expires=600)],
    })
    assert response.status_code == 202
    assert service_one() is True
    done = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    decisions = {row["车辆"]: row for row in done["result"]["rows"]}
    # 410 分回执仍是最新一条且在 480 已失效，YP-D22 继续停放。
    assert decisions["YP-D22"]["放行决定"] == "停放"
    assert "证据已失效" in decisions["YP-D22"]["证据诊断"]


def test_signed_handover_is_never_recomputed_or_overwritten(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)
    first_digest = run["digest"]
    signed = client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                         json={"note": "按第 1 版签署留存"}).json()
    assert signed["result_version"] == 1 and signed["result_digest"] == first_digest

    outcome = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                          json={"receipts": [d22_brake("RC-AFTER-SIGN")]}).json()
    # 已签核批次不受影响，不进入重算。
    assert outcome["affected_run_ids"] == [] and outcome["recomputing"] is False
    assert service_one() is False  # 队列里没有任务。
    detail = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert detail["version"] == 1 and detail["digest"] == first_digest
    review = client.get(f"/runs/{run['id']}/review", headers=auth["reviewer"]).json()
    assert review["note"] == "按第 1 版签署留存"
    exported = client.get(f"/runs/{run['id']}/export", headers=auth["viewer"]).json()
    assert exported["digest"] == first_digest and exported["review"]["result_version"] == 1


def test_concurrent_backfill_and_sign_lands_on_one_valid_version(client, auth, monkeypatch):
    """补登与签核同时提交：行锁串行化，签核只能落在复核员看到且仍有效的版本。"""
    from fastapi import HTTPException

    from src.config.resources import engine
    from src.schemas.reviews import ReviewInput
    from src.services import backfills, reviews as review_service

    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)

    # 补登事务先提交：未签核批次转入重算并加锁持有到提交为止。
    with engine.begin() as db:
        outcome = backfills.register(db, case_id, [d22_brake("RC-RACE")], "operator")
    assert outcome["affected_run_ids"] == [run["id"]]

    # 签核事务后到：批次已暂停，状态闸门拒绝，签署不会落在失效版本上。
    with engine.begin() as db:
        with pytest.raises(HTTPException) as blocked:
            review_service.sign(db, run["id"], ReviewInput(note="补登进行中尝试签署"), "reviewer")
        assert blocked.value.status_code == 409
    assert client.get(f"/runs/{run['id']}/review", headers=auth["reviewer"]).json() is None

    assert service_one() is True
    refreshed = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert refreshed["version"] == 2
    # 重算完成后签署，落版必须是复核员此刻看到且仍有效的第 2 版。
    signed = client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                         json={"note": "重算完成后签署第 2 版"}).json()
    assert signed["result_version"] == 2 and signed["result_digest"] == refreshed["digest"]


def test_unaffected_case_keeps_normal_handover(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_a, _ = make_case(client, auth["operator"], REGULAR_PAYLOAD)
    other_payload = {
        "vehicles": [{"asset": "YP-T99", "category": "电动"}],
        "requirements": [
            {"category": "电动", "check": "制动"},
            {"category": "电动", "check": "牵引销"},
        ],
        "evidence": [
            {"seq": 1, "asset": "YP-T99", "check": "制动", "checked": 450, "expires": 620, "status": "pass"},
            {"seq": 2, "asset": "YP-T99", "check": "牵引销", "checked": 451, "expires": 620, "status": "pass"},
        ],
    }
    case_b, _ = make_case(client, auth["operator"], other_payload)
    run_a = run_to_done(client, auth["operator"], case_a)
    run_b = run_to_done(client, auth["operator"], case_b)

    outcome = client.post(f"/cases/{case_a}/backfills", headers=auth["operator"],
                          json={"receipts": [d22_brake("RC-ONLY-A")]}).json()
    assert outcome["affected_run_ids"] == [run_a["id"]]
    # B 资料的批次照常办理：直接签核、下载均可用。
    detail_b = client.get(f"/runs/{run_b['id']}", headers=auth["operator"]).json()
    assert detail_b["status"] == JobState.DONE and detail_b["version"] == 1
    assert client.post(f"/runs/{run_b['id']}/review", headers=auth["reviewer"],
                       json={"note": "未受影响批次照常签核"}).status_code == 201
    assert client.get(f"/runs/{run_b['id']}/export", headers=auth["viewer"]).status_code == 200


def test_failed_recompute_blocks_handover_until_retry_succeeds(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)

    client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                json={"receipts": [d22_brake("RC-BOOM")]})
    # worker 取走任务后让场景计算报错一次，模拟重算失败，续跑时恢复。
    from src.domain import scenario as scenario_module

    real_run = scenario_module.run
    attempts = {"count": 0}

    def boom(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("模拟重算失败")
        return real_run(*args, **kwargs)

    monkeypatch.setattr("src.services.execution.scenario.run", boom)
    try:
        service_one()
    except JobFailure as exc:
        # 与 worker 主循环一致：捕获后落失败状态。
        fail(exc)
    failed = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert failed["status"] == JobState.FAILED and "模拟重算失败" in failed["failure"]
    assert client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                       json={"note": "失败状态不应签署"}).status_code == 409
    assert client.get(f"/runs/{run['id']}/export", headers=auth["viewer"]).status_code == 409

    # 续跑：恢复正确口径后重新入队并算完，交接随之恢复。
    patched_scenario(monkeypatch)
    retry = client.post(f"/runs/{run['id']}/retry", headers=auth["operator"])
    assert retry.status_code == 202 and retry.json()["status"] == JobState.QUEUED
    assert service_one() is True
    done = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert done["status"] == JobState.DONE and done["version"] == 2
    assert client.post(f"/runs/{run['id']}/review", headers=auth["reviewer"],
                       json={"note": "续跑成功后签署"}).status_code == 201


def test_retry_only_allowed_for_failed_runs(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)
    assert client.post(f"/runs/{run['id']}/retry", headers=auth["operator"]).status_code == 409


def test_orphaned_recomputing_run_is_reclaimed_by_next_worker(client, auth, monkeypatch):
    """worker 在重算中崩溃：行锁释放后，残留的 recomputing 行可被重新领取并算完。"""
    from sqlalchemy import update

    from src.config.resources import engine
    from src.models.runs import runs

    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    run = run_to_done(client, auth["operator"], case_id)
    client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                json={"receipts": [d22_brake("RC-ORPHAN")]})
    # 模拟 worker 取走任务、切到 recomputing 后崩溃，行未被任何事务持有。
    with engine.begin() as db:
        db.execute(update(runs).where(runs.c.id == run["id"]).values(status=JobState.RECOMPUTING))

    assert service_one() is True
    done = client.get(f"/runs/{run['id']}", headers=auth["operator"]).json()
    assert done["status"] == JobState.DONE and done["version"] == 2
    decisions = {row["车辆"]: row for row in done["result"]["rows"]}
    assert decisions["YP-D22"]["放行决定"] == "放行"


def test_backfill_requires_operator_and_valid_payload(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    # reviewer 不能补登。
    denied = client.post(f"/cases/{case_id}/backfills", headers=auth["reviewer"],
                         json={"receipts": [d22_brake("RC-X")]})
    assert denied.status_code == 403
    # 未登录被拒。
    assert client.post(f"/cases/{case_id}/backfills",
                       json={"receipts": [d22_brake("RC-X")]}).status_code == 401
    # 业务校验不通过返回 422。
    bad = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                      json={"receipts": [d22_brake("RC-BAD", asset="YP-GHOST")]})
    assert bad.status_code == 422


def test_backfill_ledger_is_listable(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                json={"receipts": [d22_brake("RC-LEDGER")]})
    ledger = client.get(f"/cases/{case_id}/backfills", headers=auth["viewer"]).json()
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["receipt_key"] == "RC-LEDGER" and entry["seq"] == 7 and entry["actor"] == "operator"


def test_backfill_skips_signed_run_but_recomputes_unsigned_sibling(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    signed_run = run_to_done(client, auth["operator"], case_id, parameter=470)
    unsigned_run = run_to_done(client, auth["operator"], case_id, parameter=490)
    client.post(f"/runs/{signed_run['id']}/review", headers=auth["reviewer"],
                json={"note": "先签署较早批次"})

    outcome = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                          json={"receipts": [d22_brake("RC-MIX")]}).json()
    assert outcome["affected_run_ids"] == [unsigned_run["id"]]
    assert service_one() is True
    untouched = client.get(f"/runs/{signed_run['id']}", headers=auth["operator"]).json()
    updated = client.get(f"/runs/{unsigned_run['id']}", headers=auth["operator"]).json()
    assert untouched["version"] == 1 and untouched["status"] == JobState.DONE
    assert updated["version"] == 2 and updated["revision"] == 1
    # 已签核批次仍可正常下载，且内容是签署时的版本。
    exported = client.get(f"/runs/{signed_run['id']}/export", headers=auth["viewer"]).json()
    assert exported["review"]["result_version"] == 1


def test_backfill_before_first_execution_feeds_first_run_without_version_bump(client, auth, monkeypatch):
    patched_scenario(monkeypatch)
    case_id, _ = make_case(client, auth["operator"])
    # 只入队，尚未执行就补登。
    queued = client.post(f"/cases/{case_id}/runs", headers=auth["operator"],
                         json={"parameter": 480}).json()
    outcome = client.post(f"/cases/{case_id}/backfills", headers=auth["operator"],
                          json={"receipts": [d22_brake("RC-EARLY")]}).json()
    assert outcome["affected_run_ids"] == [queued["id"]]
    assert service_one() is True
    done = client.get(f"/runs/{queued['id']}", headers=auth["operator"]).json()
    # 首跑即包含补登证据，但不算重算：版本仍是第 1 版。
    assert done["status"] == JobState.DONE and done["version"] == 1 and done["revision"] == 0
    decisions = {row["车辆"]: row for row in done["result"]["rows"]}
    assert decisions["YP-D22"]["放行决定"] == "放行"
