from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.constants.job_state import BLOCKED_STATES, JobState
from src.domain.scenario import CONFIG
from src.models.cases import cases
from src.models.runs import runs
from . import audit, cases as case_service


def query():
    return select(runs, cases.c.title.label("case_title")).join(cases, cases.c.id == runs.c.case_id)


def get(db, run_id, lock=False):
    statement = query().where(runs.c.id == run_id)
    if lock:
        statement = statement.with_for_update()
    run = db.execute(statement).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    return dict(run)


def list_entries(db):
    return [dict(row) for row in db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]


def enqueue(db, case_id, parameter, actor):
    if parameter > CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {CONFIG['max_parameter']}")
    case_service.get(db, case_id)
    change = db.execute(insert(runs).values(
        case_id=case_id, status=JobState.QUEUED, parameter=parameter, actor=actor, created_at=int(time())
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)


def ensure_handover_open(run):
    """受影响批次在重算期间暂停签核与下载；已签核批次不经过此闸门。"""
    if run["status"] in BLOCKED_STATES:
        labels = {
            JobState.QUEUED: "批次正在排队处理",
            JobState.RECOMPUTING: "补登回执重算中",
            JobState.FAILED: "重算失败，等待续跑",
        }
        raise HTTPException(409, f"批次当前「{labels[run['status']]}」，恢复交接后才能办理")
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "批次执行成功后才能办理")


def retry(db, run_id, actor):
    """重算失败后续跑：重新入队，成功才恢复交接，未受影响的批次无需经过这里。"""
    run = get(db, run_id, lock=True)
    if run["status"] != JobState.FAILED:
        raise HTTPException(409, "只有失败的批次可以续跑")
    db.execute(runs.update().where(runs.c.id == run_id).values(
        status=JobState.QUEUED, failure=None,
    ))
    audit.record(db, "retry", actor, "run", run_id)
    return get(db, run_id)
