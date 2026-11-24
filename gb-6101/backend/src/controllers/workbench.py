from redis.exceptions import RedisError
from src.config.resources import cache, engine
from src.services import backfills, cases, runs, reviews


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    _wake(run["id"])
    return run


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def new_backfill(case_id, data, user):
    with engine.begin() as db:
        outcome = backfills.register(db, case_id, [item.model_dump() for item in data.receipts], user["username"])
    for run_id in outcome["affected_run_ids"]:
        _wake(run_id)
    return outcome


def retry_run(run_id, user):
    with engine.begin() as db:
        run = runs.retry(db, run_id, user["username"])
    _wake(run_id)
    return run


def _wake(run_id):
    try:
        cache.lpush("workbench:wake", str(run_id))
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass
