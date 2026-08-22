"""Rate limiting, backed by Redis so the limit is per-deployment rather than per-worker.

A limiter held in process memory divides the real limit by however many workers happen to
be running, which means the number in `policy.py` is not the number that applies. Redis
makes the configured value the actual value.

The algorithm is a token bucket implemented in one Lua script, so check-and-consume is
atomic. Doing it as GET-then-SET in Python is a race two concurrent requests win
together, and a limiter that can be beaten by sending requests at the same moment is
exactly no limiter under the load that matters."""

from dataclasses import dataclass

from redis.asyncio import Redis

#: Refill is continuous rather than a fixed window: a fixed window lets a caller spend
#: its whole allowance at the boundary and again immediately after, so the observed peak
#: is twice the configured rate. KEYS[1] holds the bucket; ARGV carries capacity, refill
#: rate per second, now, and the cost of this request.
_TAKE = """
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(state[1])
local updated = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  updated = now
end

tokens = math.min(capacity, tokens + (now - updated) * refill_per_second)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
-- Expiry so an idle caller's bucket is reclaimed. Long enough to refill from empty,
-- because a key that vanishes early hands back a full bucket for free.
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill_per_second) + 60)

return allowed
"""


@dataclass(frozen=True)
class Limiter:
    """One named limit. `per_hour` is the sustained rate; `burst` is how much can be
    spent at once, which is what makes a short flurry of legitimate clicks succeed
    without raising the sustained rate to cover it."""

    name: str
    per_hour: int
    burst: int


class RateLimiter:
    def __init__(self, redis_client: Redis, limiters: dict[str, Limiter]) -> None:
        self._redis = redis_client
        self._limiters = limiters
        self._script = redis_client.register_script(_TAKE)

    async def allow(self, limit_name: str, identity: str, now: float) -> bool:
        """True if this request fits within `limit_name` for `identity`.

        A name with no configured limiter is allowed rather than denied: the route table
        is the authority on what is exposed, and a typo there must not silently become a
        closed door that looks like a bug in the limiter."""
        limiter = self._limiters.get(limit_name)
        if limiter is None:
            return True

        allowed = await self._script(
            keys=[f"ratelimit:{limiter.name}:{identity}"],
            args=[limiter.burst, limiter.per_hour / 3600.0, now, 1],
        )
        return bool(allowed)
