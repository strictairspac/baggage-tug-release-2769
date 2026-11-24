from fastapi import APIRouter, Depends

from src.config.resources import engine
from src.controllers.access import identity, operator
from src.controllers.workbench import new_backfill
from src.schemas.backfills import BackfillInput, BackfillResult, BackfillReceiptEntry
from src.services import backfills, cases

router = APIRouter(prefix="/cases/{case_id}/backfills", tags=["Receipt Backfill"])


@router.get("", response_model=list[BackfillReceiptEntry], dependencies=[Depends(identity)])
def list_backfills(case_id: int):
    with engine.connect() as db:
        cases.get(db, case_id)
        return backfills.list_entries(db, case_id)


@router.post("", response_model=BackfillResult, status_code=202)
def submit_backfill(case_id: int, form: BackfillInput, user=Depends(operator)):
    return new_backfill(case_id, form, user)
