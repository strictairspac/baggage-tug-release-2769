from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    RECOMPUTING = "recomputing"
    DONE = "done"
    FAILED = "failed"


# 补登回执重算期间，未签核批次需要暂停的对外办理动作。
BLOCKED_STATES = frozenset({JobState.QUEUED, JobState.RECOMPUTING, JobState.FAILED})
