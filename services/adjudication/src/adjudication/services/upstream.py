"""The one exception both upstream clients raise, so the pipeline has a single type to
catch regardless of which service failed."""


class UpstreamUnavailable(Exception):
    """`policy` or `member` did not answer: a timeout, a connection error, or any
    non-2xx response except the one 404 that `member_client.coverage` treats as a
    meaningful answer (no record of this member).

    When the pipeline catches this, the case escalates with reason
    `insufficient_evidence` -- not a dedicated "upstream unavailable" reason, because
    `determinations.reason` is a closed four-value CHECK constraint (see
    migrations/0001_cases_and_determinations.sql) and a fifth value would mean a
    migration plus a change to packages/common, the one coupling point. It is also the
    honest description: the system could not obtain the evidence it needed."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service} unavailable: {detail}")
        self.service = service
        self.detail = detail
