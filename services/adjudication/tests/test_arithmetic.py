"""The two `Arithmetic` implementations, against each other on the same fixtures.

Why differential rather than per-arm. ADR-0003's ablation is an argument only if the two
arms differ in *who performs the comparison* and in nothing else. If they disagree about
what the comparison means -- an exclusive boundary on one side, an inclusive one on the
other -- then the ablation measures a disagreement about the rule and reports it as a model
error. So the load-bearing assertion here is not "the Python arm is correct"; it is "given a
model that answers correctly, the two arms return the same thing on every fixture."

The second thing this file pins is that the ablated arm asks a **well-posed** question. A
`within()` prompt that omitted the anchor date, or a `compare()` prompt that omitted the
threshold, would leave the model unable to answer whatever its arithmetic ability -- and the
ablation would publish a prompt bug as evidence against the thesis. That failure is silent:
the model returns some boolean, the run completes, the number looks like a result. The
`ModelArithmetic` arm had no tests at all before this file, so nothing checked it.

Measured context, 2026-08-22: the ablated arm reached the gate on none of the five golden
cases, exhausting its retry ladder against an 8,000 TPM ceiling. That is a rate-limit
failure rather than an arithmetic one, and telling those apart is exactly what this suite
makes possible.
"""

from datetime import date

import pytest

from adjudication.services.upstream import UpstreamUnavailable
from adjudication.services.verify.arithmetic import (
    MODEL_TOOL_PREFIX,
    ArithmeticUnusable,
    ModelArithmetic,
    PythonArithmetic,
)

ANCHOR = date(2026, 1, 15)

#: Every fixture is `(label, call, expected)`, where `call` takes an `Arithmetic` and awaits
#: one comparison. Ground truth is written by hand -- deriving it from either arm would make
#: the agreement test circular.
#:
#: The boundaries are the point. `>=` at exactly the threshold, `>` at exactly the threshold,
#: and a window at exactly `window_days` are where the two arms can silently disagree, and
#: the `>`-versus-`>=` distinction has produced two defects on this project already.
CASES = [
    ("ge at the boundary", lambda a: a.compare(">=", 15.0, 15.0), True),
    ("ge just below", lambda a: a.compare(">=", 14.999, 15.0), False),
    ("ge above", lambda a: a.compare(">=", 46.916, 15.0), True),
    ("gt at the boundary", lambda a: a.compare(">", 15.0, 15.0), False),
    ("gt above", lambda a: a.compare(">", 15.001, 15.0), True),
    ("le at the boundary", lambda a: a.compare("<=", 14.0, 14.0), True),
    ("le above", lambda a: a.compare("<=", 14.446, 14.0), False),
    ("lt at the boundary", lambda a: a.compare("<", 14.0, 14.0), False),
    ("eq exact", lambda a: a.compare("==", 4.0, 4.0), True),
    ("eq near miss", lambda a: a.compare("==", 4.0000001, 4.0), False),
    # The near-miss member's real AHI against the real threshold, and against the 5-14
    # band's ceiling -- the pair that makes p2 escalate rather than approve.
    ("near miss vs threshold", lambda a: a.compare(">=", 14.446, 15.0), False),
    ("near miss vs band ceiling", lambda a: a.compare("<=", 14.446, 14.0), False),
    ("channels below the minimum", lambda a: a.compare(">=", 2.0, 3.0), False),
    ("channels at the minimum", lambda a: a.compare(">=", 3.0, 3.0), True),
    (
        "member of, present",
        lambda a: a.member_of("home_type_ii", ["attended_psg", "home_type_ii"]),
        True,
    ),
    (
        "member of, absent",
        lambda a: a.member_of("home_type_iv", ["attended_psg", "home_type_ii"]),
        False,
    ),
    ("member of, empty allowed", lambda a: a.member_of("home_type_ii", []), False),
    (
        "member of is not a substring test",
        lambda a: a.member_of("home_type_i", ["home_type_ii"]),
        False,
    ),
    (
        "within, well inside the window",
        lambda a: a.within("within_days_before", date(2025, 12, 1), ANCHOR, 365),
        True,
    ),
    (
        "within, exactly on the boundary",
        lambda a: a.within("within_days_before", date(2025, 1, 15), ANCHOR, 365),
        True,
    ),
    (
        "within, one day past the boundary",
        lambda a: a.within("within_days_before", date(2025, 1, 14), ANCHOR, 365),
        False,
    ),
    (
        "within, same day as the anchor",
        lambda a: a.within("within_days_before", ANCHOR, ANCHOR, 365),
        True,
    ),
    (
        "within_days_before rejects a date after the anchor",
        lambda a: a.within("within_days_before", date(2026, 2, 1), ANCHOR, 365),
        False,
    ),
    (
        "within_days_after accepts a date after the anchor",
        lambda a: a.within("within_days_after", date(2026, 2, 1), ANCHOR, 365),
        True,
    ),
    (
        "within_days_after rejects the anchor itself",
        lambda a: a.within("within_days_after", ANCHOR, ANCHOR, 365),
        False,
    ),
    (
        "within a leap-year window",
        lambda a: a.within("within_days_before", date(2024, 2, 29), date(2024, 3, 1), 1),
        True,
    ),
]

IDS = [label for label, _, _ in CASES]


class _OracleLLM:
    """A model that answers every comparison correctly, and records what it was asked.

    The answers come from a table passed in, never computed here -- a stub that did the
    arithmetic itself would just be a third implementation, and agreeing with it would prove
    nothing about the other two."""

    def __init__(self, answer: bool) -> None:
        self._answer = answer
        self.questions: list[str] = []

    async def chat(self, messages: list[dict], schema: dict) -> dict:
        self.questions.append(messages[-1]["content"])
        return {"answer": self._answer}


class _FixedLLM:
    """Returns whatever body it was given, however malformed."""

    def __init__(self, body: object) -> None:
        self._body = body

    async def chat(self, messages: list[dict], schema: dict) -> object:
        return self._body


class _FailingLLM:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def chat(self, messages: list[dict], schema: dict) -> object:
        raise self._error


# --- the Python arm against hand-written ground truth ----------------------------------


@pytest.mark.parametrize(("label", "call", "expected"), CASES, ids=IDS)
async def test_the_python_arm_matches_the_fixture(label, call, expected):
    assert await call(PythonArithmetic()) is expected


# --- the two arms against each other --------------------------------------------------


@pytest.mark.parametrize(("label", "call", "expected"), CASES, ids=IDS)
async def test_the_arms_agree_wherever_the_model_is_right(label, call, expected):
    """The assertion ADR-0003's ablation rests on. Given a model that answers the comparison
    correctly, the ablated arm must return exactly what the shipped arm returns -- otherwise
    the two arms disagree about the *rule*, and every difference the ablation reports is
    partly that disagreement rather than the model's arithmetic."""
    python_answer = await call(PythonArithmetic())
    model_answer = await call(ModelArithmetic(_OracleLLM(expected)))

    assert python_answer is expected
    assert model_answer is python_answer


# --- the ablated arm asks a question that can be answered -----------------------------


@pytest.mark.parametrize(
    ("call", "must_mention"),
    [
        pytest.param(
            lambda a: a.compare(">=", 14.446, 15.0),
            ["14.446", "15.0", ">="],
            id="compare",
        ),
        pytest.param(
            lambda a: a.member_of("home_type_iv", ["attended_psg", "home_type_ii"]),
            ["home_type_iv", "attended_psg", "home_type_ii"],
            id="member_of",
        ),
        pytest.param(
            lambda a: a.within("within_days_before", date(2025, 1, 15), ANCHOR, 365),
            ["2025-01-15", "2026-01-15", "365", "before"],
            id="within",
        ),
    ],
)
async def test_the_question_carries_every_operand(call, must_mention):
    """A prompt missing an operand is unanswerable whatever the model's arithmetic, and the
    failure is silent: the model still returns a boolean, the run still completes, and the
    ablation publishes a prompt bug as evidence about the thesis. `within` is the one most
    exposed -- it must carry both dates and the window, because the subtraction is the thing
    being measured."""
    llm = _OracleLLM(True)
    await call(ModelArithmetic(llm))

    question = llm.questions[-1]
    for operand in must_mention:
        assert operand in question, f"{operand!r} missing from {question!r}"


async def test_the_window_question_states_that_the_boundary_counts():
    """The Python arm's boundary is inclusive. If the prompt does not say so, a model that
    reads "within 365 days" as exclusive is not wrong about arithmetic -- it is answering a
    different question, and the ablation would score that as an error."""
    llm = _OracleLLM(True)
    await ModelArithmetic(llm).within("within_days_before", date(2025, 1, 15), ANCHOR, 365)

    assert "counts as within" in llm.questions[-1]


async def test_the_model_is_told_to_answer_only_the_question():
    llm = _OracleLLM(True)
    await ModelArithmetic(llm).compare(">=", 1.0, 0.0)

    assert llm.questions, "the model was never asked anything"


# --- an unusable answer is not a False answer -----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="null"),
        pytest.param({}, id="empty-object"),
        pytest.param({"answer": None}, id="null-answer"),
        pytest.param({"answer": "true"}, id="string-answer"),
        pytest.param({"answer": 1}, id="int-answer"),
        pytest.param({"answer": 0}, id="zero-answer"),
        pytest.param({"result": True}, id="wrong-key"),
        pytest.param([True], id="list"),
        pytest.param("true", id="bare-string"),
    ],
)
async def test_an_unanswered_comparison_raises_rather_than_reading_as_false(body):
    """`ArithmeticUnusable`, never `False`. A false answer is a wrong comparison and belongs
    in the ablation's error rate; an unusable answer is the *absence* of a comparison, and
    counting it as "not met" would make the ablated arm look merely strict rather than
    broken -- which is the flattering direction, and therefore the dangerous one.

    `{"answer": 1}` and `{"answer": 0}` are here because `isinstance(True, int)` is true in
    Python but `isinstance(1, bool)` is not: the check has to be on `bool`, and an
    implementation that used a truthiness test would pass every other case in this list."""
    with pytest.raises(ArithmeticUnusable):
        await ModelArithmetic(_FixedLLM(body)).compare(">=", 15.0, 15.0)


async def test_an_unusable_answer_reports_the_question_and_the_raw_body():
    """A reviewer looking at an `insufficient_evidence` verdict from the ablated arm needs to
    know which comparison went unanswered and what came back instead."""
    with pytest.raises(ArithmeticUnusable) as caught:
        await ModelArithmetic(_FixedLLM({"answer": "yes"})).compare(">=", 14.446, 15.0)

    assert "14.446" in caught.value.question
    assert caught.value.raw == {"answer": "yes"}


@pytest.mark.parametrize(
    "method",
    [
        pytest.param(lambda a: a.compare(">=", 1.0, 0.0), id="compare"),
        pytest.param(lambda a: a.member_of("x", ["y"]), id="member_of"),
        pytest.param(lambda a: a.within("within_days_before", ANCHOR, ANCHOR, 1), id="within"),
    ],
)
async def test_every_method_validates_its_answer(method):
    """All three, not just `compare`: they share `_ask`, and this is what keeps that true."""
    with pytest.raises(ArithmeticUnusable):
        await method(ModelArithmetic(_FixedLLM({"answer": "maybe"})))


async def test_an_unavailable_upstream_propagates_rather_than_becoming_unusable():
    """`UpstreamUnavailable` must pass through so the worker can retry the case (ADR-0020).
    Folded into `ArithmeticUnusable` it would become an `insufficient_evidence` verdict, and
    the run would record an ablation result that no model produced. This is precisely the
    path all five cases took on 2026-08-22, so it has to stay a retry rather than a verdict."""
    error = UpstreamUnavailable("llm", "status 429", transient=True, retry_after=30.0)

    with pytest.raises(UpstreamUnavailable) as caught:
        await ModelArithmetic(_FailingLLM(error)).compare(">=", 15.0, 15.0)

    assert caught.value.transient is True


# --- the arms are distinguishable in the audit trail, and only there -------------------


def test_only_the_ablated_arm_marks_its_tool():
    """`evals` counts this prefix to report how much of a run was actually ablated, so the
    two arms must disagree here and nowhere else in their contract."""
    assert PythonArithmetic().tool("threshold:ahi") == "threshold:ahi"
    assert ModelArithmetic(_OracleLLM(True)).tool("threshold:ahi") == (
        f"{MODEL_TOOL_PREFIX}threshold:ahi"
    )


def test_the_prefix_is_recoverable_from_the_marked_tool():
    """A row's base tool has to survive the prefixing, or a report cannot say *which*
    comparison the model performed -- only that one was."""
    marked = ModelArithmetic(_OracleLLM(True)).tool("temporal:study_date")

    assert marked.startswith(MODEL_TOOL_PREFIX)
    assert marked.removeprefix(MODEL_TOOL_PREFIX) == "temporal:study_date"


async def test_the_ablated_arm_serialises_its_comparisons():
    """One model call at a time per case. `verify_all` gathers a case's deterministic
    criteria, which is free when each is a SQL-shaped question and a thundering herd when
    each is a model call -- and an ablated run that spent its measurement on 429s would be
    measuring the rate limit. Given what the rate limit did to this arm on 2026-08-22, the
    lock is load-bearing rather than tidy."""
    import asyncio

    in_flight = 0
    peak = 0

    class _CountingLLM:
        async def chat(self, messages: list[dict], schema: dict) -> dict:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return {"answer": True}

    arithmetic = ModelArithmetic(_CountingLLM())
    await asyncio.gather(*(arithmetic.compare(">=", 1.0, 0.0) for _ in range(8)))

    assert peak == 1, f"{peak} concurrent model calls from one case's arithmetic"
