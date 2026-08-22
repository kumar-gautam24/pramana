"""The route table.

This module is the artifact a reviewer reads to answer "what is exposed, and to whom".
It deliberately imports nothing but the standard library: the table should be legible
without following an import into an HTTP client or a settings object.

**A path that is absent is unroutable**, which is a stronger guarantee than a path that
is guarded. Two categories are absent on purpose:

- `member`'s endpoints. The console never reads a member's chart directly; only
  `adjudication` does, as part of deciding a case. Exposing them would make the clinical
  record browsable through the front door, which is not a capability this system needs.
- Every write that seeds or ingests (`/seed`, `/ingest`). Those are operator actions run
  against a service directly, and a fixture loader reachable from the internet is a
  liability with no matching benefit.

`principal` is the minimum role a caller must hold. `None` means public, and exactly two
routes are: logging in, and liveness."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    """One public path and everything the gateway needs to serve it.

    `limit` names a limiter in `policy.py` rather than holding numbers, so this table
    stays about routing and that module stays about values."""

    method: str
    path: str
    upstream: str
    #: A role name from auth's own vocabulary, or a pseudo-role: "session" means any
    #: authenticated user will do. None means no session required at all.
    principal: str | None = None
    timeout: float = 15.0
    stream: bool = False
    limit: str | None = None
    #: The prefix stripped from the public path to get the upstream's own path.
    #:
    #: Declared per route rather than derived, because the two differ: `/api` is this
    #: gateway's namespace, but `auth` groups its routes under `/api/auth` here while
    #: serving them at its own root (`/login`, not `/auth/login`). Assuming a single
    #: `/api` strip 404s every auth route -- which it did, until this field existed.
    strip: str = "/api"


#: Roles that satisfy each `principal` requirement. `admin` appears in every set: an
#: admin who could not do what an operator can would just be a fourth role.
#:
#: `clinician` can reach the review routes and `reviewer` cannot, which is not a
#: hierarchy but a legal distinction: Illinois permits only a clinical peer to issue an
#: adverse determination, so recording a review is a clinician's act specifically.
SATISFIES: dict[str, frozenset[str]] = {
    "session": frozenset({"clinician", "reviewer", "operator", "admin"}),
    "clinician": frozenset({"clinician", "admin"}),
    "operator": frozenset({"operator", "admin"}),
    "admin": frozenset({"admin"}),
}


ROUTES: tuple[Route, ...] = (
    # --- auth -----------------------------------------------------------------------
    Route("POST", "/api/auth/login", "auth", None, timeout=10, limit="login", strip="/api/auth"),
    Route("POST", "/api/auth/logout", "auth", "session", timeout=10, strip="/api/auth"),
    Route("GET", "/api/auth/session", "auth", "session", timeout=10, strip="/api/auth"),
    Route("POST", "/api/auth/users", "auth", "admin", timeout=10, strip="/api/auth"),
    Route("GET", "/api/auth/users", "auth", "admin", timeout=10, strip="/api/auth"),
    # --- cases ----------------------------------------------------------------------
    # A case submission returns 202 immediately -- the worker adjudicates it -- so this
    # needs no long timeout despite what it sets in motion.
    Route("POST", "/api/cases", "adjudication", "session", timeout=15),
    Route("GET", "/api/cases", "adjudication", "session"),
    Route("GET", "/api/cases/{case_id}", "adjudication", "session"),
    Route("GET", "/api/cases/{case_id}/events", "adjudication", "session"),
    Route("GET", "/api/cases/{case_id}/criteria", "adjudication", "session"),
    Route("GET", "/api/cases/{case_id}/reviews", "adjudication", "session"),
    # The live audit surface. stream=True removes the read timeout entirely: a long gap
    # between SSE frames is the normal shape of a pipeline waiting on a model, not a
    # failure, and buffering this route would defeat the only reason it exists.
    Route("GET", "/api/cases/{case_id}/stream", "adjudication", "session", stream=True),
    # Recording a review is a clinician's act -- see SATISFIES above.
    Route("POST", "/api/cases/{case_id}/review", "adjudication", "clinician", timeout=15),
    # --- evals ----------------------------------------------------------------------
    Route("GET", "/api/golden-cases", "evals", "operator"),
    Route("POST", "/api/golden-cases", "evals", "operator"),
    # A run submits every golden case and waits on each determination, so it is the one
    # route whose own timeout is generous by design.
    Route("POST", "/api/eval-runs", "evals", "operator", timeout=120, limit="eval_run"),
    Route("GET", "/api/eval-runs", "evals", "operator"),
    Route("GET", "/api/eval-runs/{run_id}", "evals", "operator"),
    # A run beside its ablated twin, with the delta -- the comparison the ablation exists
    # for. Declared after the run route above and matched independently of it: a path
    # parameter never spans a `/`, so `/api/eval-runs/7/comparison` cannot resolve to the
    # single-run handler.
    Route("GET", "/api/eval-runs/{run_id}/comparison", "evals", "operator"),
    # --- policy ---------------------------------------------------------------------
    Route("POST", "/api/policies/search", "policy", "session", timeout=30),
)
