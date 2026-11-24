import logging
from time import time

from sqlalchemy import select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.models.cases import cases
from src.models.runs import runs
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)


def service_one():
    with engine.begin() as db:
        # 同时回收残留的 recomputing 行：worker 崩溃后行锁释放，另一个 worker 可安全续领。
        statement = (
            select(runs).where(runs.c.status.in_((JobState.QUEUED, JobState.RECOMPUTING)))
            .order_by(runs.c.id).limit(1).with_for_update(skip_locked=True)
        )
        task = db.execute(statement).mappings().first()
        if task is None:
            return False
        recompute = task["revision"] > 0
        active = JobState.RECOMPUTING if recompute else JobState.QUEUED
        db.execute(update(runs).where(runs.c.id == task["id"]).values(status=active))
        payload = db.scalar(select(cases.c.payload).where(cases.c.id == task["case_id"]))
        try:
            result = scenario.run(payload, task["parameter"], db)
            output = {
                "status": JobState.DONE, "result": result, "digest": content_digest(result),
                "failure": None, "finished_at": int(time()),
                # 补登重算成功才升版；首跑保持第 1 版。
                "version": task["version"] + 1 if recompute else task["version"],
            }
        except Exception as exc:
            # Let the failed database transaction roll back before persisting the error.
            raise JobFailure(task["id"], exc, recompute) from exc
        finish(db, task["id"], output, recompute)
        return True


class JobFailure(Exception):
    def __init__(self, run_id, cause, recompute=False):
        super().__init__(str(cause))
        self.run_id = run_id
        self.cause = cause
        self.recompute = recompute


def fail(job_failure):
    with engine.begin() as db:
        run_id = job_failure.run_id
        statement = select(runs).where(runs.c.id == run_id).with_for_update()
        task = db.execute(statement).mappings().first()
        if task is None or task["status"] not in (JobState.QUEUED, JobState.RECOMPUTING):
            return
        cause = job_failure.cause
        if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
            reason = str(cause) or "输入资料或批次参数不符合要求"
        else:
            reason = "处理失败，请检查 worker 日志"
        # 失败后停留 failed：已算过的批次保留上一版结果，续跑由补登/重试接口重新入队。
        finish(db, run_id, {"status": JobState.FAILED, "failure": reason[:320]}, job_failure.recompute)


def finish(db, run_id, output, recompute=False):
    db.execute(update(runs).where(runs.c.id == run_id).values(**output))
    record(db, "recomputed" if recompute and output["status"] == JobState.DONE else str(output["status"]),
           "worker", "run", run_id)
