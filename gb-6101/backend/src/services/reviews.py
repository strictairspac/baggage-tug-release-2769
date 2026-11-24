from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.models.reviews import reviews
from . import audit, runs as run_service


def get(db, run_id):
    row = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def sign(db, run_id, data, actor):
    run = run_service.get(db, run_id, lock=True)
    # 暂停中的批次不允许签核；补登与签核并发时，先拿行锁的一方落库，后一方看到最新状态。
    run_service.ensure_handover_open(run)
    try:
        db.execute(insert(reviews).values(
            run_id=run_id, note=data.note, actor=actor, created_at=int(time()),
            result_version=run["version"], result_digest=run["digest"] or "",
        ))
    except IntegrityError as exc:
        raise HTTPException(409, "该批次已经签核") from exc
    audit.record(db, "review", actor, "run", run_id)
    return get(db, run_id)
