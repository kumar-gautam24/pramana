"""Who performs the comparison — the one thing the `model_arithmetic` ablation changes.

[ADR-0003](../../../../../docs/decisions/0003-ai-extracts-rules-code-checks-facts.md) says
the model decides what the rules are and deterministic code checks the facts they point at.
Its own consequences section promises that claim will be *measured* rather than asserted: an
ablation runs the pipeline with the model performing the date and threshold comparisons, and
publishes the error rate. This module is that seam.

`deterministic.py` fetches the member's facts, decides whether there is a document to read at
all, and assembles the evidence. Every question it needs answered with a yes or a no comes
through the `Arithmetic` protocol below. `PythonArithmetic` answers with the `operator`
module, membership and `timedelta` — the shipped behaviour, unchanged. `ModelArithmetic`
answers by asking the model.

**Nothing else differs between the two arms**, and that is the point rather than an
implementation convenience: the same fetches, the same missing-versus-contradicted rules, the
same "any study before the date of service satisfies it" semantics, the same evidence, the
same gate, the same thresholds. A comparison of two runs that differed in more than one place
would be an anecdote. This is the only place the difference is allowed to live.

Three things were deliberately kept identical that a looser ablation would have varied.

**Confidence stays 1.0 in both arms.** Asking the model to report its own confidence would be
a second difference, and it would move the ablated run along a different point of the
threshold sweep for a reason that has nothing to do with whether the arithmetic was right.
The model's own words are kept in the evidence instead, where they inform a reader without
entering the gate.

**One model call per comparison, deliberately not batched.** Batching judgment criteria was a
clear win (ADR-0015) because each was a separate question about one shared document. These
are separate questions about *different numbers*, and putting them in one context lets one
answer condition the next — which would change what is being measured. The cost is real: an
ablated case spends one call per comparison instead of none. That cost is the finding, not an
overhead to optimise away.

**The prompts ask the bare question.** No worked example, no instruction to think step by
step, no policy framing — just the comparison, at temperature 0, with a two-key schema. That
is the framing most favourable to the model, which makes any error rate measured here a lower
bound on what a system built this way would actually produce. A harder prompt would make the
ablation easier to win and worth less.
"""

import asyncio
import operator as _op
from datetime import date
from typing import Any, Protocol

from adjudication.services.llm import LLMProvider

#: The closed `domain.params.THRESHOLD_OPERATORS` set, mapped to callables once. That the
#: mapping exists in exactly one place is what makes a `>=`-for-`>` typo a one-line diff to
#: find rather than a bug hiding in a chain of `if`/`elif` -- the `>`-versus-`>=` distinction
#: has produced two defects in this project already. A member of that set with no entry here
#: raises `KeyError` at verification time, which is the loud failure wanted.
_COMPARATORS = {
    ">=": _op.ge,
    ">": _op.gt,
    "<=": _op.le,
    "<": _op.lt,
    "==": _op.eq,
}

#: The prefix `deterministic.py` puts on `criterion_results.tool` for an ablated comparison,
#: so a criterion row says which arm produced it without anyone having to join back to the
#: case. `evals` counts these to report how much of a run was actually ablated.
MODEL_TOOL_PREFIX = "model_arithmetic:"

#: Two keys and one boolean. The smaller the schema, the fewer ways a model has to answer
#: something that is not an answer -- and an unusable answer here is indistinguishable from a
#: wrong one unless the schema makes it loud.
_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "boolean"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class ArithmeticUnusable(Exception):
    """The model was asked a comparison and did not answer it.

    Not folded into `False`. A false answer is a wrong comparison and belongs in the
    ablation's error rate; an unusable answer is the absence of a comparison, and counting it
    as "not met" would make the ablated arm look merely strict rather than broken.
    `services/verify/__init__.py` turns this into an `INSUFFICIENT_EVIDENCE` verdict, the same
    resolution `judgment.py` gives a model response it cannot read."""

    def __init__(self, question: str, raw: object) -> None:
        super().__init__(f"model did not answer the comparison {question!r}: {raw!r}")
        self.question = question
        self.raw = raw


class Arithmetic(Protocol):
    """Every yes/no question `deterministic.py` needs answered.

    Three methods rather than one, because the three comparisons carry different arguments
    and collapsing them into a generic `compare(op, left, right)` would make the model
    prompts generic too -- and a prompt that says "is 20180612 within 365 of 20190101" is
    measuring string handling rather than date arithmetic.

    Every method is `async` even though the Python implementation never awaits: the protocol
    has to accommodate the arm that makes a network call, and a sync/async split here would
    duplicate every verifier in `deterministic.py`."""

    def tool(self, base: str) -> str:
        """Decorate `criterion_results.tool` so a row records which arm verified it."""
        ...

    async def compare(self, operator: str, observed: float, threshold: float) -> bool:
        """`observed <operator> threshold`, where `operator` is from `THRESHOLD_OPERATORS`."""
        ...

    async def member_of(self, observed: str, allowed: list[str]) -> bool:
        """Whether `observed` is one of `allowed`."""
        ...

    async def within(
        self, operator: str, subject: date, anchor: date, window_days: int
    ) -> bool:
        """Whether `subject` falls in the `window_days` window `operator` describes relative
        to `anchor` (the case's date of service, the only temporal anchor a case has).

        Takes the two dates rather than a day count on purpose: subtracting them **is** the
        date arithmetic the ablation exists to test, so handing an implementation the answer
        would leave nothing to ablate."""
        ...


def _within_days(operator: str, days_from_anchor: int, window_days: int) -> bool:
    """The window rule, in one place so both arms describe the same window.

    `within_days_before`'s boundary is inclusive in both directions: a study exactly
    `window_days` days before the date of service counts, one day further back does not.
    `within_days_after` is written out rather than assumed unreachable -- `member_client`'s
    "on or before" cutoff means no study after the date of service can be returned today, so
    a future fact with a real "after" fetch is covered for free."""
    if operator == "within_days_before":
        return 0 <= days_from_anchor <= window_days
    return -window_days <= days_from_anchor < 0


class PythonArithmetic:
    """The shipped arm: `operator` module callables, `in`, and `date` subtraction. This is
    the code that was in `deterministic.py` before the ablation existed, moved rather than
    rewritten, so the arm the system actually ships on did not change when its twin arrived."""

    def tool(self, base: str) -> str:
        return base

    async def compare(self, operator: str, observed: float, threshold: float) -> bool:
        return _COMPARATORS[operator](observed, threshold)

    async def member_of(self, observed: str, allowed: list[str]) -> bool:
        return observed in allowed

    async def within(
        self, operator: str, subject: date, anchor: date, window_days: int
    ) -> bool:
        return _within_days(operator, (anchor - subject).days, window_days)


class ModelArithmetic:
    """The ablated arm: the same three questions, asked of the model.

    Every answer is validated before it is believed -- a body that is not a dict, or has no
    boolean `answer`, raises `ArithmeticUnusable` rather than being read as `False`. That
    is the same discipline `judgment.py` applies to a verdict it cannot parse, and it is what
    keeps "the model got the comparison wrong" separate from "the model did not answer",
    which are different findings.

    `UpstreamUnavailable` is not caught here. It propagates exactly as it does from extraction
    and judgment, so a rate-limited ablation retries in the worker (ADR-0020) rather than
    quietly recording an ablation result that no model produced."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        #: One comparison at a time, per case.
        #:
        #: `verify_all` runs a case's deterministic criteria through one `asyncio.gather`,
        #: which costs nothing when each is a SQL-shaped question to `member` and is a
        #: thundering herd when each is a model call. The worker already serialises *cases*
        #: for exactly this reason (a provider metering tokens per minute), and an ablated
        #: run that spent its measurement on 429s would be measuring the rate limit rather
        #: than the arithmetic.
        #:
        #: This changes how long an ablated case takes, never what it answers -- the
        #: comparisons are independent, so their order and overlap cannot affect a verdict.
        #: One instance per case (see `services/verify.__init__._arithmetic`), so the lock
        #: is per case too.
        self._one_at_a_time = asyncio.Lock()

    def tool(self, base: str) -> str:
        return f"{MODEL_TOOL_PREFIX}{base}"

    async def _ask(self, question: str) -> bool:
        messages = [
            {
                "role": "system",
                "content": (
                    "Answer the question with JSON of the form {\"answer\": true} or "
                    "{\"answer\": false}. Answer only the question asked."
                ),
            },
            {"role": "user", "content": question},
        ]
        async with self._one_at_a_time:
            raw = await self._llm.chat(messages, _ANSWER_SCHEMA)
        if not isinstance(raw, dict) or not isinstance(raw.get("answer"), bool):
            raise ArithmeticUnusable(question, raw)
        return raw["answer"]

    async def compare(self, operator: str, observed: float, threshold: float) -> bool:
        return await self._ask(f"Is {observed} {operator} {threshold}?")

    async def member_of(self, observed: str, allowed: list[str]) -> bool:
        listed = ", ".join(allowed)
        return await self._ask(f"Is the value {observed} one of the following: {listed}?")

    async def within(
        self, operator: str, subject: date, anchor: date, window_days: int
    ) -> bool:
        direction = "before" if operator == "within_days_before" else "after"
        # Both dates, and the window, in the question -- the subtraction is the thing being
        # measured. "On the boundary counts" is stated because the Python arm's boundary is
        # inclusive and an ablation that differed on the boundary would be measuring a
        # disagreement about the rule rather than about the arithmetic.
        return await self._ask(
            f"Is {subject.isoformat()} within {window_days} days {direction} "
            f"{anchor.isoformat()}? A date exactly {window_days} days {direction} counts as "
            f"within."
        )


#: One instance is enough: it holds nothing. The ablated arm has to be constructed per case
#: because it holds the provider.
PYTHON_ARITHMETIC = PythonArithmetic()
