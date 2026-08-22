"""The single front door: app assembly, the middleware-free request path, and the
lifespan probes.

Every public route is built from `routes.ROUTES` at import, so the route table is the
only place a path is declared. There is no catch-all proxy: a path absent from that table
does not resolve, which is a stronger guarantee than one that resolves and is then
checked.

Order of work per request, and the reason for it:

1. **Circuit breaker** -- before anything else, because the whole point is to not spend
   work on an upstream already known to be failing.
2. **Authentication** -- resolve the caller against `auth`.
3. **Authorisation** -- does that caller's role satisfy this route.
4. **Rate limit** -- keyed on the resolved user where there is one, falling back to the
   caller's address for the routes that have no session yet (logging in). Keying on the
   user rather than the address means one clinician behind a hospital NAT cannot exhaust
   the allowance of everyone else behind it.
5. **Proxy**, streaming the response.

Authorisation precedes the rate limit deliberately: a forbidden request should be told so
regardless of how many it has sent, and letting an unauthorised caller consume a
legitimate user's bucket would be a denial-of-service vector rather than a protection."""

import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from gateway import proxy
from gateway.breaker import CircuitBreaker
from gateway.config import get_settings, upstream_urls
from gateway.limits import Limiter, RateLimiter
from gateway.policy import Policy
from gateway.principal import Forbidden, Principals, Unauthenticated, authorise
from gateway.routes import ROUTES, Route


def _limiters() -> dict[str, Limiter]:
    return {
        "session": Limiter("session", Policy.SESSION_PER_HOUR, Policy.SESSION_BURST),
        "login": Limiter("login", Policy.LOGIN_PER_HOUR, Policy.LOGIN_BURST),
        "eval_run": Limiter("eval_run", Policy.EVAL_RUN_PER_HOUR, Policy.EVAL_RUN_BURST),
    }


def _client_address(request: Request, trusted_hops: int) -> str:
    """The caller's address, honouring only as many X-Forwarded-For hops as are actually
    deployed in front of this process.

    Trusting the whole header would let a caller prepend any address it likes and reset
    its own rate-limit bucket at will, which is why `trusted_proxy_hops` defaults to 0
    and must be set deliberately."""
    if trusted_hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if len(hops) >= trusted_hops:
            return hops[-trusted_hops]
    return request.client.host if request.client else "unknown"


def _token_from(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("pramana_session", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    urls = upstream_urls(settings)

    # A route naming an upstream that does not exist is a mistake in the route table, and
    # it must surface at boot rather than as a 500 the first time that path is called.
    unknown = sorted({route.upstream for route in ROUTES} - set(urls))
    if unknown:
        raise RuntimeError(f"routes.py names upstreams with no configured URL: {unknown}")

    app.state.settings = settings
    app.state.urls = urls
    app.state.client = httpx.AsyncClient()
    app.state.breaker = CircuitBreaker(
        failures=Policy.BREAKER_FAILURES, cooldown_seconds=Policy.BREAKER_COOLDOWN_SECONDS
    )
    app.state.principals = Principals(app.state.client, settings.auth_url)

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis_client.ping()
    app.state.redis = redis_client
    app.state.limiter = RateLimiter(redis_client, _limiters())

    # Every upstream probed at startup, so a misconfigured address fails here with the
    # URL in the message rather than on a caller's first request. A non-2xx is fine --
    # what is being proven is that something answers at that address.
    for name, base_url in urls.items():
        try:
            await app.state.client.get(f"{base_url}/health", timeout=Policy.CONNECT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"upstream {name} unreachable at {base_url}: {exc}") from exc

    yield

    await app.state.client.aclose()
    await redis_client.aclose()


def _handler_for(route: Route):
    async def handler(request: Request):
        app = request.app
        now = time.monotonic()

        if app.state.breaker.is_open(route.upstream, now):
            # 503 with Retry-After, not 502: this is the gateway declining to try, and a
            # caller that knows when to come back does not need to poll to find out.
            raise HTTPException(
                status_code=503,
                detail=f"{route.upstream} is failing; not attempting this request",
                headers={"Retry-After": str(int(Policy.BREAKER_COOLDOWN_SECONDS))},
            )

        token = _token_from(request)
        identity = _client_address(request, app.state.settings.trusted_proxy_hops)

        if route.principal is not None:
            try:
                caller = await app.state.principals.resolve(token)
            except Unauthenticated as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

            try:
                authorise(caller, route.principal)
            except Forbidden as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc

            request.state.principal = caller
            # Keyed on the user, not the address -- see the module docstring.
            identity = caller.user_id

        limit_name = route.limit or ("session" if route.principal else None)
        if limit_name is not None and not await app.state.limiter.allow(
            limit_name, identity, time.time()
        ):
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": "60"},
            )

        try:
            response = await proxy.forward(
                route, request, app.state.urls[route.upstream], app.state.client
            )
        except HTTPException:
            # 502 and 504 from the proxy are what the breaker counts. A 4xx from the
            # upstream is not a failure of the upstream -- it is the upstream working --
            # and counting those would open the circuit on a caller's bad input.
            app.state.breaker.record_failure(route.upstream, now)
            raise

        app.state.breaker.record_success(route.upstream)

        # A logout that reached auth must invalidate the gateway's cached view of that
        # session immediately, or the token would keep working for the cache window --
        # the one case where SESSION_CACHE_SECONDS would be a real security gap rather
        # than an acceptable staleness.
        if route.path == "/api/auth/logout" and response.status_code // 100 == 2:
            app.state.principals.forget(token)

        return response

    return handler


settings = get_settings()
app = FastAPI(
    title="pramana-gateway",
    lifespan=lifespan,
    # The interactive docs enumerate every route; useful locally, needless attack surface
    # in production.
    docs_url=None if settings.env == "production" else "/docs",
    redoc_url=None,
)

# The console is served from a different origin in development, so it needs CORS. Origins
# are not wildcarded: credentials travel on these requests, and `*` with credentials is
# both refused by browsers and wrong in principle.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

for _route in ROUTES:
    app.add_api_route(
        _route.path,
        _handler_for(_route),
        methods=[_route.method],
        # Excluded from the schema: the handler's real request and response shapes belong
        # to the upstream that owns them, and advertising this signature would describe
        # the proxy rather than the API.
        include_in_schema=False,
    )


@app.get("/health")
async def health() -> dict:
    """Liveness only. Deliberately does not touch Redis or any upstream: a health check
    that fails because something behind it is down causes a restart that fixes nothing."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request) -> dict:
    """Readiness, which does check what this process needs in order to serve: Redis, and
    that each upstream answers. Reports per-upstream rather than one boolean, because
    "which one is down" is the first thing anyone reading this wants to know."""
    upstreams: dict[str, str] = {}
    for name, base_url in request.app.state.urls.items():
        if request.app.state.breaker.is_open(name, time.monotonic()):
            upstreams[name] = "circuit open"
            continue
        try:
            await request.app.state.client.get(
                f"{base_url}/health", timeout=Policy.CONNECT_TIMEOUT
            )
            upstreams[name] = "ok"
        except httpx.HTTPError as exc:
            upstreams[name] = f"unreachable: {type(exc).__name__}"

    try:
        await request.app.state.redis.ping()
        redis_status = "ok"
    except Exception as exc:
        redis_status = f"unreachable: {type(exc).__name__}"

    ready = redis_status == "ok" and all(value == "ok" for value in upstreams.values())
    return {
        "status": "ready" if ready else "degraded",
        "redis": redis_status,
        "upstreams": upstreams,
    }
