# ADR-0017 — The gateway establishes identity; nothing downstream may claim it

**Status:** accepted, 2026-08-22

## Context

Seven services sit behind one address. `adjudication` needs to know which clinician recorded a
review — that row is the record of who issued an adverse determination, which is the fact
Illinois law is specifically about — and it must not learn it from the request body, where a
caller could attribute a decision to a colleague.

The usual answer is a header: the gateway resolves the session and writes
`X-Pramana-User-Id` and `X-Pramana-Role` for the upstream to read. That works exactly as long
as no one can send those headers themselves. The moment they can, the header is not a fact the
gateway established, it is a claim the caller made, and every downstream authorisation check
built on it is decoration.

There is a second version of the same question. A gateway that proxies everything and checks a
list of protected paths is only as good as that list: a path nobody thought to protect is a
path that is served.

## Decision

**Headers in the `x-pramana-` namespace are stripped from every inbound request and written
only by the gateway.** Alongside `x-forwarded-` and `x-real-ip`, they are removed before the
request is forwarded, unconditionally, for every route. Nothing downstream trusts a caller's
copy because a caller's copy never arrives.

**A path absent from the route table is unroutable.** There is no catch-all proxy. Every public
path is declared in `routes.py` with its upstream, its timeout, its rate limiter and the minimum
role it requires, and the app is assembled from that table at import. An unlisted path 404s
because nothing serves it — a stronger guarantee than one that resolves and is then checked.
Two categories are absent on purpose: `member`'s endpoints, so the clinical record is not
browsable through the front door, and every `/seed` and `/ingest` write, because a fixture
loader reachable from the internet is a liability with no matching benefit.

**Identity has one authority.** The gateway holds no user table and no password; it forwards
the caller's token to `auth`'s `/session` and believes the answer. Two implementations of "is
this token still good" would eventually disagree, and one of them would be wrong about a
revoked session. Answers are cached for seconds, not minutes, because the cache window is
exactly how long a logged-out token keeps working — and a proxied logout invalidates the entry
immediately rather than waiting it out.

**Auth failures fail closed.** `auth` being unreachable is not authorisation: an outage denies
requests rather than admitting them.

**Roles are a legal distinction, not a hierarchy.** `clinician` may reach the review routes and
`reviewer` may not, because only a clinical peer may issue an adverse determination. `admin`
satisfies every requirement, since an admin who could not do what an operator can would just be
a fourth role.

Ordering within a request is deliberate: circuit breaker, then authentication, then
authorisation, then rate limit, then proxy. Authorisation precedes the rate limit so a
forbidden request is told so however many it has sent, and so an unauthorised caller cannot
consume a legitimate user's bucket — which would be a denial-of-service vector wearing the
costume of a protection.

## Consequences

"The caller's role" is a fact this system establishes rather than a claim it receives, and that
property holds by construction rather than by every service remembering to check. Verified by
running it: a spoofed `X-Pramana-Role: admin` is ignored, a clinician gets 403 on operator-only
and admin-only routes, `member`'s endpoints and `/seed` are 404 at the front door, and a logout
401s the very next request.

The route table becomes the artifact a reviewer reads to answer "what is exposed, and to whom".
It imports nothing but the standard library so it stays legible without following an import
into an HTTP client, and it is the only place a path is declared.

The cost is that every new public path is an edit to that table, including its role and its
timeout. That is the point — the alternative is a path that works without anyone having decided
who may use it.

Validating a session on every request means `auth` is on the hot path of everything. The short
cache bounds the load; failing closed bounds the risk. If `auth` is down, nothing is served,
which is the correct direction for a service standing in front of clinical decisions.

The console holds `NEXT_PUBLIC_GATEWAY_URL` and no other backend address, which is what makes
all of the above load-bearing rather than advisory: there is no second door for a browser to
find.

Related: [ADR-0002](0002-no-deny-path.md), [ADR-0005](0005-case-events-as-audit-log.md)
