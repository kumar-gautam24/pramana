# gateway

**The only public surface.** Port 8000, no database.

Every request from the console or any other client arrives here. The gateway decides three
things, in this order, before anything reaches a service:

1. **Is this upstream worth trying?** A circuit breaker opens after 5 consecutive failures and
   stays open for 30 seconds. It is checked first, because the point is to *not* spend a
   clinician's wait discovering an upstream is down.
2. **Who is calling?** The session token is resolved against `auth` here and nowhere else.
3. **May that caller use this route?** A route table maps method and path to an upstream and a
   minimum role.

Only then does it proxy.

## Why identity is established here

The gateway strips every inbound `x-pramana-*` header and writes its own. That is what makes
"the caller's role" a fact the system establishes rather than one a client asserts — without it,
anything that could reach a service directly could send `x-pramana-role: clinician` and be
believed. A clinician is the only role permitted to decide a case, so that header is the whole
authorisation model ([ADR-0017](../../docs/decisions/0017-gateway-establishes-identity.md)).

The console holds this service's address and no other backend address, so there is no second
door for a browser to find.

## The route table

Routing is a table, not a chain of conditionals, so "who can reach what" is one thing to read.
`None` means public; `session` means any authenticated caller.

| method | path | upstream | minimum role |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | auth | — (public) |
| POST | `/api/auth/logout` | auth | session |
| GET | `/api/auth/session` | auth | session |
| POST · GET | `/api/auth/users` | auth | admin |
| POST · GET | `/api/cases` | adjudication | session |
| GET | `/api/cases/{id}` | adjudication | session |
| GET | `/api/cases/{id}/events` | adjudication | session |
| GET | `/api/cases/{id}/criteria` | adjudication | session |
| GET | `/api/cases/{id}/reviews` | adjudication | session |
| GET | `/api/cases/{id}/stream` | adjudication | session (SSE) |
| POST | `/api/cases/{id}/review` | adjudication | **clinician** |
| POST · GET | `/api/golden-cases` | evals | operator |
| POST · GET | `/api/eval-runs` | evals | operator |
| GET | `/api/eval-runs/{id}` | evals | operator |
| GET | `/api/eval-runs/{id}/comparison` | evals | operator |
| POST | `/api/policies/search` | policy | session |
| GET | `/health` · `/ready` | — | — (public) |

`admin` satisfies `operator`: an admin who could not do what an operator can would just be a
fourth role.

Recording a review is gated to `clinician` because Illinois permits only a clinical peer to
issue an adverse determination. That is a legal constraint expressed as a table row.

## Rate limits

Backed by Redis, so the limit is per-deployment rather than per-worker. A limiter in process
memory divides the real limit by however many workers happen to be running, and one that can be
beaten by sending requests simultaneously is no limiter under the load that matters.

| limit | sustained | burst | why |
| --- | --- | --- | --- |
| `session` | 600/hour | 30 | a clinician works a queue steadily, not in bursts of hundreds |
| `login` | 60/hour | 10 | credential stuffing |
| `eval_run` | 2/hour | 2 | an eval run is tens of minutes of paced model calls |

A route naming a limit that has no configured limiter is **allowed**, not denied. The route
table is the source of truth for what exists; a typo there should surface as a missing limit,
not as a closed door that looks like a limiter bug.

## Configuration

| variable | purpose |
| --- | --- |
| `REDIS_URL` | sessions and rate-limit counters |
| `AUTH_URL` · `POLICY_URL` · `ADJUDICATION_URL` · `EVALS_URL` | upstreams |
| `TRUSTED_PROXY_HOPS` | how many hops to trust for the client address. **Zero in compose.** Trusting more hops than exist lets a caller spoof its own address, and rate limits are per-address. |

## Running it

Started by compose. On its own:

```bash
cd services/gateway
uv run uvicorn gateway.main:app --port 8000
```

It has no database and no migrations. It does require `auth` and Redis to be reachable.

## Caveats

- **No test suite.** This service is carried substantially from
  [Deflect](https://github.com/kumar-gautam24/deflect), where it was reviewed and ran under
  production-shaped conditions, and it was reviewed again here — but it has no tests of its own
  in this repository.
- **It cannot protect a published service port.** The gateway enforces the admin check on
  `POST /api/auth/users`; `auth` publishes 8004 in the compose file, so addressing that port
  directly bypasses the gateway entirely. See the [auth README](../auth/README.md).
- **`/ready` returns a bare response**, so FastAPI generates no OpenAPI schema for it.
