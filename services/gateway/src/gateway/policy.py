"""Gateway constants in one place, each with the reason it has that value.

A number without its justification is a number nobody can safely change six months
later."""


class Policy:
    # A clinician working a queue opens cases steadily, not in bursts of hundreds. This
    # is generous for that and still bounds a runaway client.
    SESSION_PER_HOUR = 600
    SESSION_BURST = 30

    # Per-address, not per-account: an attacker guessing passwords has no account yet.
    # A backstop against credential stuffing rather than the primary control, and sized
    # so filling this bucket cannot also lock a legitimate user out.
    LOGIN_PER_HOUR = 60
    LOGIN_BURST = 10

    # An eval run costs real model tokens and takes minutes. Two an hour is enough to
    # iterate and few enough that a stuck loop cannot spend a budget unattended.
    EVAL_RUN_PER_HOUR = 2
    EVAL_RUN_BURST = 2

    WINDOW_SECONDS = 3600

    # Connect and write are short because a healthy upstream on the same private network
    # answers in milliseconds; a slow one is a failure, not a slow success. Read is
    # per-route, since an eval run legitimately takes far longer than a case fetch.
    CONNECT_TIMEOUT = 5.0
    WRITE_TIMEOUT = 5.0

    # Five consecutive failures is a pattern rather than a blip. Thirty seconds is long
    # enough for a restart to finish and short enough that recovery is not itself noticed
    # as an outage. Starting points, chosen rather than defaulted.
    BREAKER_FAILURES = 5
    BREAKER_COOLDOWN_SECONDS = 30

    # How long a validated session is trusted without re-asking auth. Short, because it
    # bounds how long a logged-out token keeps working: the window in which a revoked
    # session is still honoured is exactly this value.
    SESSION_CACHE_SECONDS = 10
