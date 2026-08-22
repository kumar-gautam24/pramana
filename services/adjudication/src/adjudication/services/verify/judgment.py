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


class _BatchVerdict(_JudgmentResponse):
    """One criterion's answer inside a batch, tagged with which criterion it is.

    `index` rather than the criterion's own id: the id is a database key, and handing a
    model a key it could echo into the wrong field is a class of confusion worth not
    inviting. A position in a list the prompt itself numbers is checked exactly as
    strictly (see `_match`) and means nothing outside this one call."""

    index: int = Field(ge=0)


class _BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[_BatchVerdict]


_BATCH_SCHEMA = _BatchResponse.model_json_schema()


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


def _build_batch_messages(criteria: list[Criterion], notes: list[Note]) -> list[dict[str, str]]:
    """One call carrying every judgment criterion for a case, against notes sent once.

    Verifying these one at a time re-sent the member's whole chart per criterion: a real
    case extracted seven judgment criteria and so spent seven model calls and seven
    copies of the notes to read the same three sentences. Batching makes it two calls per
    case (one extraction, one judgment round) and is what brings a case inside a
    rate-limited token budget at all.

    The criteria stay *individually answered* -- each gets its own verdict, confidence and
    spans, and each is grounded and validated separately below. This changes how many
    round trips the notes make, not how independently each criterion is judged."""
    notes_text = "\n\n".join(f"[note {n.id}, {n.date.isoformat()}] {n.text}" for n in notes)
    listed = "\n".join(f"{i}. {criterion.text}" for i, criterion in enumerate(criteria))
    system = (
        "You are assessing prior-authorization criteria against a member's clinical "
        "notes. Decide each criterion independently, using only what the notes say.\n\n"
        "Respond with JSON: `verdicts` is a list with exactly one entry per criterion "
        "below. Each entry has `index` (the criterion's number as shown), `verdict` "
        '(exactly one of "met", "not_met", "insufficient_evidence" -- use '
        "insufficient_evidence when the notes do not say enough to decide either way; "
        "never guess), `confidence` (0 to 1, given only the notes shown), and "
        "`evidence_spans` (short quotes copied verbatim, character for character, from "
        "the notes -- do not paraphrase, summarise, or write anything not literally "
        "present). Judging one criterion met must not make you more willing to call "
        "another met: they are separate questions about the same notes."
    )
    user = f"Criteria:\n{listed}\n\nClinical notes:\n{notes_text}"
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


def _no_notes(criterion: Criterion) -> Verification:
    # No notes is the same shape of fact as no sleep studies in `deterministic.py` --
    # there is no document to read, so this is INSUFFICIENT_EVIDENCE with the code's own
    # certainty (1.0), not a model call over an empty prompt.
    return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, {
        "reason": "no clinical notes on record before the date of service",
    })


def _unusable(criterion: Criterion, raw: object) -> Verification:
    # Covers an unparseable body, a `verdict` outside the closed set Verdict defines,
    # and -- in a batch -- an entry that never arrived for this criterion at all. A
    # model that answers with something we cannot use must never be read as MET, and
    # must never crash the pipeline.
    return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, {
        "reason": "model returned an unparseable, out-of-range or missing response",
        "raw_response": raw,
    })


def _grounded_verification(
    criterion: Criterion, parsed: _JudgmentResponse, verdict: Verdict, notes: list[Note]
) -> Verification:
    """Span grounding and evidence assembly -- shared by the single and batched paths so
    the two cannot drift on the one rule that stops a fabricated quote reaching a
    reviewer."""
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
        # Kept, not discarded -- a reviewer benefits from seeing what the model claimed
        # but could not be verified, even though it played no part in the verdict above.
        evidence["ungrounded_spans"] = ungrounded

    return _build(criterion, verdict, parsed.confidence, evidence)


async def verify(
    criterion: Criterion, case: Case, member_client: MemberClient, llm: LLMProvider
) -> Verification:
    notes = await member_client.notes(case.member_id, case.date_of_service)
    if not notes:
        return _no_notes(criterion)

    raw = await llm.chat(_build_messages(criterion, notes), _SCHEMA)
    try:
        parsed = _JudgmentResponse.model_validate(raw)
        verdict = Verdict(parsed.verdict)
    except (ValidationError, ValueError):
        return _unusable(criterion, raw)

    return _grounded_verification(criterion, parsed, verdict, notes)


async def verify_many(
    criteria: list[Criterion], case: Case, member_client: MemberClient, llm: LLMProvider
) -> list[Verification]:
    """Every judgment criterion for one case, in one model call, returned in the order
    given. See `_build_batch_messages` for why this exists.

    A criterion the model simply omits from its answer becomes INSUFFICIENT_EVIDENCE
    rather than being dropped: the pipeline aggregates by criterion id and a missing
    result would raise `MissingCriterionResult`, so silence has to resolve to a verdict
    here. It resolves to the one that cannot approve."""
    if not criteria:
        return []

    notes = await member_client.notes(case.member_id, case.date_of_service)
    if not notes:
        return [_no_notes(criterion) for criterion in criteria]

    raw = await llm.chat(_build_batch_messages(criteria, notes), _BATCH_SCHEMA)
    try:
        batch = _BatchResponse.model_validate(raw)
    except ValidationError:
        # The whole answer is unusable, so every criterion in it is -- one per criterion
        # rather than one exception, because the pipeline needs a result for each.
        return [_unusable(criterion, raw) for criterion in criteria]

    # Last entry wins on a duplicated index rather than first: a model that corrects
    # itself mid-list is likelier than one whose first word is its considered answer.
    # Either way an index outside the list is discarded, so a stray number cannot
    # silently overwrite a real criterion's verdict.
    by_index = {
        entry.index: entry for entry in batch.verdicts if 0 <= entry.index < len(criteria)
    }

    verifications = []
    for index, criterion in enumerate(criteria):
        entry = by_index.get(index)
        if entry is None:
            verifications.append(_unusable(criterion, raw))
            continue
        try:
            verdict = Verdict(entry.verdict)
        except ValueError:
            verifications.append(_unusable(criterion, raw))
            continue
        verifications.append(_grounded_verification(criterion, entry, verdict, notes))

    return verifications
