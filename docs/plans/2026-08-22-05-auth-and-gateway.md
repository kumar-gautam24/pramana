# Pramana Plan 05 — Auth and Gateway

> **Written retroactively, 2026-08-22.** This work was built in one pass during the plan 04
> build-out rather than planned first, and this document records what was built and why, in
> the place the plan sequence expects to find it. Every checkbox is marked done because every
> one of them is; the value here is the decisions and the deferrals, not the schedule. Do not
> read it as a plan that was followed.

**Goal:** Give the system a front door and an identity. `auth` (8004) owns accounts, sessions
and roles; `gateway` (8000) is the single address anything outside the compose network may
talk to.

**Architecture:** `auth` is a FastAPI service owning `pramana_auth`, layered
`routers → services → repositories → models` with pure logic in `domain/`. `gateway` owns no
database at all: it holds a declarative route table, resolves each caller against `auth`, gates
on role, rate-limits in Redis, trips a circuit breaker per upstream, and streams the proxied
response.

**Tech stack:** Python 3.12, uv, FastAPI, asyncpg, Postgres 16, Redis, httpx, argon2-cffi,
pytest, ruff.

## Global constraints

- Database per service. `gateway` has none; `auth` owns `pramana_auth` and nothing else.
- Raw SQL over asyncpg, numbered `.sql` migrations (ADR-0013).
- **Only a clinical peer may issue an adverse determination** (Illinois). Roles are a legal
  fact, not a convenience, and the schema treats them as one.
- No plaintext password is stored, logged or returned by any route.
- Comments explain **why**, never what.
- Commits: conventional, imperative, lowercase subject. **Never any attribution trailer.**

---

### Task 1: `auth` — schema, passwords, sessions

**Files:** `services/auth/{pyproject.toml,Dockerfile}`, `migrations/0001_users_and_sessions.sql`,
`src/auth/{config,db,main}.py`, `src/auth/domain/passwords.py`, `src/auth/models/`,
`src/auth/repositories/`, `src/auth/services/accounts.py`, `src/auth/routers/{auth,health}.py`.

```
users     id, email UNIQUE, password_hash, role CHECK(clinician|reviewer|operator|admin), created_at
sessions  id, token_hash UNIQUE, user_id FK, expires_at, created_at
```

- [x] **Passwords are argon2id.** The encoded string carries its own salt and parameters, so
  nothing about the hash needs storing alongside it.
- [x] **The session token is stored as a SHA-256, not as itself, and not as argon2.** A leaked
  database must not hand the reader live sessions. SHA-256 rather than a password hash is a
  deliberate difference in kind: 256 bits of CSPRNG output has no dictionary to defend against,
  and this value is validated on *every* proxied request, so a deliberately slow hash here would
  be a per-request cost buying nothing.
- [x] **`role` is a CHECK constraint and a closed enum.** A typo'd role that silently becomes a
  new unhandled value is how an authorisation check ends up passing something it should have
  refused.
- [x] **`expires_at` is NOT NULL.** A session with no expiry can only be revoked by someone
  acting; time is the one revocation that needs nobody.
- [x] **Login answers 401 identically for an unknown address and a wrong password**, and hashes
  a throwaway on the unknown path so response time is not an enumeration oracle.
- [x] **Logout is 200 whether or not a session went away.** A 404 would tell a caller that a
  token they hold is not a real one.
- [x] **`/seed` is guarded on the users table being empty**, not on the address being absent —
  the guard has to be "this system has no accounts yet", or seeding becomes a way to add one.

---

### Task 2: `gateway` — the route table

**Files:** `services/gateway/{pyproject.toml,Dockerfile}`, `src/gateway/routes.py`.

- [x] **Every public path is a `Route` in one table**, with its upstream, method, minimum role,
  timeout, rate limiter, streaming flag and the prefix to strip. The app is assembled from that
  table at import; **there is no catch-all proxy**, so a path absent from the table is
  unroutable rather than routable-and-then-checked.
- [x] **`routes.py` imports nothing but the standard library.** It is the artifact a reviewer
  reads to answer "what is exposed, and to whom", and that reading should not require following
  an import into an HTTP client or a settings object.
- [x] **`member`'s endpoints are absent on purpose**, so the clinical record is not browsable
  through the front door. So is every `/seed` and `/ingest`: a fixture loader reachable from the
  internet is a liability with no matching benefit.
- [x] **`strip` is per route, not derived.** `auth` is grouped under `/api/auth` here but serves
  at its own root, so a single `/api` strip 404s every auth route — which it did, until this
  field existed.
- [x] **`SATISFIES` maps a requirement to the roles that meet it.** `clinician` reaches the
  review routes and `reviewer` does not; that is the Illinois distinction, not a hierarchy.
  `admin` satisfies everything, because an admin who could not do what an operator can would
  just be a fourth role.

---

### Task 3: `gateway` — the request path

**Files:** `src/gateway/{main,principal,proxy,limits,breaker,policy,config}.py`.

Order per request, and the reason for it: **circuit breaker → authenticate → authorise → rate
limit → proxy.**

- [x] Breaker first, because the point is not to spend work on an upstream already known to be
  failing. It counts 502 and 504 from the proxy and not a 4xx from the upstream — a 4xx is the
  upstream working, and counting it would open the circuit on a caller's bad input.
- [x] **Authorisation precedes the rate limit.** A forbidden request should be told so however
  many it has sent, and letting an unauthorised caller consume a legitimate user's bucket would
  be a denial-of-service vector wearing the costume of a protection.
- [x] **Rate limits key on the resolved user**, falling back to the address only where there is
  no session yet. One clinician behind a hospital NAT must not exhaust everyone else's
  allowance.
- [x] **`X-Forwarded-For` is honoured only as many hops as are actually deployed**
  (`trusted_proxy_hops`, default 0). Trusting the whole header lets a caller reset its own
  bucket at will.
- [x] **Every `x-pramana-` header is stripped inbound and written by the gateway outbound.**
  This is what makes the caller's role a fact the gateway establishes rather than a claim the
  caller makes — see [ADR-0017](../decisions/0017-gateway-establishes-identity.md).
- [x] **`auth` is the single authority on identity.** The gateway holds no user table; it
  forwards the token to `/session` and believes the answer. Answers are cached for ten seconds,
  because that window is exactly how long a logged-out token keeps working — and a proxied
  logout invalidates the entry immediately rather than waiting it out.
- [x] **`auth` being unreachable is not authorisation.** It fails closed.
- [x] **The response is streamed, not buffered.** A gateway that buffers turns a token-by-token
  answer into a thirty-second wait, and the failure is invisible to any test that only checks
  the final body. `stream=True` on the SSE route removes the read timeout entirely: a long gap
  between frames is a pipeline waiting on a model, not a failure.
- [x] **Every constant lives in `policy.py` with the reason it has that value.** A number
  without its justification is a number nobody can safely change six months later.
- [x] **Startup probes every upstream**, so a misconfigured address fails at boot with the URL
  in the message. A route naming an upstream with no configured URL is a `RuntimeError` at boot,
  not a 500 on the first call. This caught a real mistake immediately: the gateway refused to
  start while `evals` did not yet exist.
- [x] `/health` is liveness only and touches nothing; `/ready` reports per upstream, because
  "which one is down" is the first thing anyone reading it wants to know.

---

### Task 4: the console's read routes on `adjudication`

**Files:** `services/adjudication/src/adjudication/routers/cases.py`.

- [x] `GET /cases` — the queue, filtered by determination outcome or pipeline status, newest
  first. A case with no determination is returned, not filtered out.
- [x] `GET /cases/{id}/criteria` — **grouped into alternative sets on the server.** Which
  criteria belong to one satisfiable set is a fact about the policy; a client reassembling it
  from a flat list could get it wrong in a way that would misrepresent why a case was refused.
  A criterion with no result is included with nulls, because omitting it would show a reviewer a
  shorter policy than the one the case was judged against.
- [x] `GET /cases/{id}/reviews` and `POST /cases/{id}/review` — the clinician is taken from the
  `X-Pramana-User-Id` header the gateway writes, **never from the body**. That row is the record
  of who made an adverse determination.

---

## Verified by running it

- All six services healthy through the front door; `/ready` reports per upstream.
- Login → session → role gating: a clinician gets 403 on operator-only and admin-only routes.
- **A spoofed `X-Pramana-Role: admin` is ignored** — stripped inbound.
- Logout invalidates the gateway's cached session immediately: 401 on the next request.
- `member`'s endpoints and `/seed` are 404 at the front door.

## Deferred out of this plan, with an owner

- **No test suite was written for either service.** The build-out ran under an explicit
  instruction to skip test-writing, and the verification above is live and manual. Both services
  sit on critical paths that CLAUDE.md requires tests for — session validation, role gating,
  header stripping, the breaker's failure accounting. **Owner: whoever next touches either
  service.** The header-stripping and `SATISFIES` cases in particular are cheap to test and
  expensive to get wrong.
- **`trusted_proxy_hops` defaults to 0** and must be set deliberately in any deployment that
  actually has a proxy in front. Recorded here because the safe default is the wrong value in
  production, and nothing will fail loudly if it is left alone.

---

## Self-review

**Coverage.** Implements the design's `gateway` and `auth` service rows, the Illinois role
gating in the regulation table, and the read surface the console needs. Does not cover: evals
(plan 06), the console itself (plan 07).

**The risk this work carries.** The gateway is the only thing standing between a browser and
six services that trust their callers. That trust is structural — `x-pramana-` stripped inbound,
no catch-all route — rather than a checklist each service repeats, which is the right shape but
means a mistake in this one file is a mistake everywhere. The absence of a test suite is the
open edge of that, and is named above rather than left to be discovered.
