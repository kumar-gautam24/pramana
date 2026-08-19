"""The `judgment` verifier -- ADR-0003's other half. `deterministic.py` compares a
fact in Python; `judgment` is for criteria that need clinical narrative no fact
captures (e.g. "documented daytime sleepiness"). The model is asked for a verdict, a
confidence, and the spans of the notes it read that support the verdict -- never for
the comparison itself, because there is none to delegate: the notes *are* the
evidence, and reading them is exactly the interpretation a deterministic tool cannot
do (see ADR-0003).

The model's answer is not trusted at face value. Two checks stand between what it says
and what this module returns:

1. **Schema and range validation** (`_JudgmentResponse`). An unparseable body, a
   `verdict` outside {met, not_met, insufficient_evidence}, or a `confidence` outside
   [0, 1] all become `INSUFFICIENT_EVIDENCE` -- never `MET`, never an unhandled
   exception reaching the pipeline.
2. **Span grounding.** A "quoted span" is only evidence if it appears verbatim in the
   notes the model was actually given. Spans that don't are dropped before being
   stored -- a fabricated-looking quote in `criterion_results.evidence` would be worse
   than no evidence, because a reviewer skimming it would trust it. If a `MET` or
   `NOT_MET` verdict has no grounded span left once ungrounded ones are dropped, the
   verdict itself is downgraded to `INSUFFICIENT_EVIDENCE`: an approval or a
   contradiction that nothing in the record actually backs is not a fact this system
   has pramana for, whichever way it points.

See `adjudication.services.verify`'s package docstring for the member-exists
invariant this module relies on the same way `deterministic.py` does.

`UpstreamUnavailable` from the notes fetch is not caught here -- it propagates to the
pipeline exactly as any other upstream failure does (see `services/upstream.py`)."""

from pramana_common.criteria import CriterionResult, Verdict
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adjudication.models.case import Case
from adjudication.models.criterion import Criterion
from adjudication.services.llm import LLMProvider
from adjudication.services.member_client import MemberClient, Note
from adjudication.services.verify import Verification

_TOOL = "judgment"


class _JudgmentResponse(BaseModel):
    # `extra="forbid"`, matching `services/extract.py`'s own raw-response model: a
    # field the model invents and this module silently ignores is a field this
    # module's docstring's promise ("validated") would be broken by.
    model_config = ConfigDict(extra="forbid")

    verdict: str
    #: Range-checked by pydantic itself, so an out-of-range value fails validation and
    #: is handled by the same `except ValidationError` as a malformed body -- one path,
    #: not two, for "the model's answer doesn't fit the shape we asked for."
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_spans: list[str] = Field(default_factory=list)


_SCHEMA = _JudgmentResponse.model_json_schema()


def _build_messages(criterion: Criterion, notes: list[Note]) -> list[dict[str, str]]:
    notes_text = "\n\n".join(f"[note {n.id}, {n.date.isoformat()}] {n.text}" for n in notes)
    system = (
        "You are assessing a single prior-authorization criterion against a member's "
        "clinical notes. Decide whether the criterion is met using only what the notes "
        "say.\n\n"
        'Respond with JSON: `verdict` is exactly one of "met", "not_met", or '
        '"insufficient_evidence" -- use insufficient_evidence when the notes do not say '
        "enough to decide either way; never guess. `confidence` is a number from 0 to 1 "
        "reflecting how certain you are given only the notes shown to you. "
        "`evidence_spans` is a list of short quotes copied verbatim, character for "
        "character, from the notes below -- do not paraphrase, summarise, or write "
        "anything that is not literally present in the notes."
    )
    user = f"Criterion: {criterion.text}\n\nClinical notes:\n{notes_text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _build(
    criterion: Criterion, verdict: Verdict, confidence: float, evidence: dict
) -> Verification:
    return Verification(
        result=CriterionResult(
            criterion_id=str(criterion.id), verdict=verdict, confidence=confidence
        ),
        tool=_TOOL,
        evidence=evidence,
    )


def _notes_checked(notes: list[Note]) -> list[dict]:
    return [{"note_id": n.id, "date": n.date.isoformat()} for n in notes]


async def verify(
    criterion: Criterion, case: Case, member_client: MemberClient, llm: LLMProvider
) -> Verification:
    notes = await member_client.notes(case.member_id, case.date_of_service)

    if not notes:
        # No notes is the same shape of fact as no sleep studies in `deterministic.py`
        # -- there is no document to read, so this is INSUFFICIENT_EVIDENCE with the
        # code's own certainty (1.0), not a model call over an empty prompt.
        return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, {
            "reason": "no clinical notes on record before the date of service",
        })

    raw = await llm.chat(_build_messages(criterion, notes), _SCHEMA)

    try:
        parsed = _JudgmentResponse.model_validate(raw)
        verdict = Verdict(parsed.verdict)
    except (ValidationError, ValueError):
        # Covers both an unparseable body and a `verdict` string outside the closed
        # set Verdict defines -- a model that answers with something we cannot use
        # must never be read as MET, and must never crash the pipeline.
        return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, {
            "reason": "model returned an unparseable or out-of-range response",
            "raw_response": raw,
        })

    notes_text = "\n".join(n.text for n in notes)
    grounded = [span for span in parsed.evidence_spans if span and span in notes_text]
    ungrounded = [span for span in parsed.evidence_spans if span not in grounded]

    if verdict is not Verdict.INSUFFICIENT_EVIDENCE and not grounded:
        return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, {
            "reason": "model's verdict cited no span that appears verbatim in the notes",
            "claimed_verdict": verdict.value,
            "model_confidence": parsed.confidence,
            "ungrounded_spans": ungrounded,
            "notes_checked": _notes_checked(notes),
        })

    evidence = {
        "criterion_text": criterion.text,
        "quoted_spans": grounded,
        "notes_checked": _notes_checked(notes),
    }
    if ungrounded:
        # Kept, not discarded -- a reviewer benefits from seeing what the model
        # claimed but could not be verified, even though it played no part in the
        # verdict above.
        evidence["ungrounded_spans"] = ungrounded

    return _build(criterion, verdict, parsed.confidence, evidence)
