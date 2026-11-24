from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.constants.job_state import JobState
from src.domain import backfill as domain
from src.models.backfills import backfills
from src.models.cases import cases
from src.models.reviews import reviews
from src.models.runs import runs
from . import audit


def list_entries(db, case_id):
    rows = db.execute(
        select(backfills).where(backfills.c.case_id == case_id).order_by(backfills.c.id)
    ).mappings().all()
    entries = []
    for row in rows:
        entry = dict(row["payload"])
        entry["id"] = row["id"]
        entry["actor"] = row["actor"]
        entries.append(entry)
    return entries


def _locked_case(db, case_id):
    row = db.execute(select(cases).where(cases.c.id == case_id).with_for_update()).mappings().first()
    if row is None:
        raise HTTPException(404, "资料不存在")
    return dict(row)


def _affected_runs(db, case_id):
    """同资料下尚未签核的批次：补登后它们持有的判定可能过期；已签核批次原样留存。"""
    signed = set(db.execute(
        select(runs.c.id).join(reviews, reviews.c.run_id == runs.c.id)
        .where(runs.c.case_id == case_id)
    ).scalars())
    rows = db.execute(
        select(runs).where(runs.c.case_id == case_id).order_by(runs.c.id).with_for_update()
    ).mappings().all()
    return [dict(row) for row in rows if row["id"] not in signed]


def _schedule_recompute(run):
    """把受影响批次切回队列；失败批次由此获得续跑机会，排队中的不重复入队。"""
    if run["status"] == JobState.RECOMPUTING:
        return False
    updates = {"status": JobState.QUEUED, "failure": None}
    if run["status"] == JobState.DONE:
        # 已完成批次进入新一轮重算，旧结果在新版本算成前暂停对外交接。
        updates.update(finished_at=None, revision=run["revision"] + 1)
    return updates


def register(db, case_id, receipts, actor):
    case = _locked_case(db, case_id)
    try:
        valid_receipts = domain.validate_batch(case["payload"], receipts)
        merged, accepted, duplicate = domain.merge_receipts(case["payload"], valid_receipts)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    stored = []
    for receipt in accepted:
        try:
            change = db.execute(insert(backfills).values(
                case_id=case_id, receipt_key=receipt["receipt_key"], payload=receipt,
                actor=actor, created_at=int(time()),
            ))
        except IntegrityError as exc:
            # 并发补登同一张回执：库内唯一约束兜底，整笔回滚后由重试方视为已入账。
            raise HTTPException(409, f"回执已补登，请勿重复入账 {receipt['receipt_key']}") from exc
        stored.append({"id": change.inserted_primary_key[0], **receipt})
        audit.record(db, "backfill", actor, "backfill", change.inserted_primary_key[0])

    affected = []
    if accepted:
        db.execute(cases.update().where(cases.c.id == case_id).values(payload=merged))
        for run in _affected_runs(db, case_id):
            updates = _schedule_recompute(run)
            if updates is False:
                affected.append(run["id"])
                continue
            db.execute(runs.update().where(runs.c.id == run["id"]).values(**updates))
            audit.record(db, "recompute", actor, "run", run["id"])
            affected.append(run["id"])

    return {
        "case_id": case_id,
        "accepted": stored,
        "duplicate": duplicate,
        "affected_run_ids": affected,
        "recomputing": bool(accepted) and bool(affected),
    }
