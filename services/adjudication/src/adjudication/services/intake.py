"""Turns a validated `POST /cases` payload into a persisted, enqueued case.

Idempotency is the UNIQUE constraint on `cases.idempotency_key`
(migrations/0003_cases_idempotency_key.sql), not a read-then-insert check in this
module: a caller-supplied key exists precisely because the caller expects to retry, and
a read-then-insert has a race that a retried submission will eventually find -- two
concurrent retries can both pass the read before either commits. `submit_case` below is
what turns the constraint's violation into "return the existing case" (task-8 brief,
decision 1) instead of a 500."""

import asyncpg

from adjudication.models.case import Case, RunMode
from adjudication.repositories import cases as cases_repo
from adjudication.services import queue

#: The name `repositories/cases.py`'s insert gives the constraint -- checked before
#: treating a `UniqueViolationError` as an idempotent replay, so a *different* unique
#: violation (there is only this one column today, but a future one is not this
#: function's problem to misdiagnose) surfaces as the bug it is rather than silently
#: returning the wrong case.
_IDEMPOTENCY_CONSTRAINT = "uq_cases_idempotency_key"


async def submit_case(
    pool,
    redis_client,
    *,
    member_id: str,
    requested_code: str,
    icd10: str,
    date_of_service,
    kind: str,
    request_text: str | None = None,
    idempotency_key: str | None = None,
    run_mode: RunMode = RunMode.DETERMINISTIC,
) -> tuple[Case, bool]:
    """Insert `case`, enqueue it for the worker, and return `(case, created)`.

    `created` is `False` exactly when `idempotency_key` was not `None` and already
    named an existing case -- the enqueue is skipped on that path (decision 1's whole
    point: a retried submission must not run through the pipeline a second time), and
    the case returned is the one the *first* submission created, not a new row.

    Note that the returned case's `run_mode` is the *first* submission's, which is one
    reason an eval fixture must not carry an `idempotency_key`: two runs of the same golden
    case would otherwise share one adjudication case, and the ablated twin would silently be
    handed the deterministic one. `evals` rejects such a fixture at authoring time."""
    try:
        case = await cases_repo.insert(
            pool,
            member_id=member_id,
            requested_code=requested_code,
            icd10=icd10,
            date_of_service=date_of_service,
            kind=kind,
            request_text=request_text,
            idempotency_key=idempotency_key,
            run_mode=run_mode,
        )
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name != _IDEMPOTENCY_CONSTRAINT or idempotency_key is None:
            raise
        existing = await cases_repo.get_by_idempotency_key(pool, idempotency_key)
        # The violation is itself proof a matching row exists; `None` here would mean
        # it vanished between that violation and this read, which nothing in this
        # service does (cases are never deleted) -- asserted, not handled, same as
        # `services/llm.py`'s provider-key asserts after Settings' own validation.
        assert existing is not None
        return existing, False

    await queue.enqueue(redis_client, case.id)
    return case, True
