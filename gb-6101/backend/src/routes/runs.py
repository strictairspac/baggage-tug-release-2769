from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.resources import engine
from src.controllers.access import identity, operator, reviewer
from src.controllers.workbench import new_review, retry_run
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import reviews, runs
from src.utils.serial import canonical_json

router = APIRouter(prefix="/runs", tags=["Execution"])


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs():
    with engine.connect() as db:
        return runs.list_entries(db)


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    with engine.connect() as db:
        return runs.get(db, run_id)


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return reviews.get(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.post("/{run_id}/retry", response_model=RunEntry, status_code=202)
def retry(run_id: int, user=Depends(operator)):
    return retry_run(run_id, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
        review = reviews.get(db, run_id)
        if review is not None:
            # 已签核交接单按原样留存：已签核批次不会再被重算，校验仅用于发现版本漂移。
            if run["digest"] != review["result_digest"] or run["version"] != review["result_version"]:
                raise HTTPException(409, "当前结果与签署落版不一致，交接单须按签署版本留存")
        else:
            # 未签核批次在重算排队/进行/失败期间暂停下载。
            runs.ensure_handover_open(run)
        run["review"] = review
    return Response(canonical_json(run), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=evidence-{run_id}.json",
    })
