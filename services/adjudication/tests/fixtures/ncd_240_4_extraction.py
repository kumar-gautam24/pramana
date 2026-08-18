"""A criteria-extraction fixture modelled on NCD 240.4 -- CPAP therapy for OSA.

**HAND-AUTHORED, NOT RECORDED.** There is no model on this development machine:
Ollama is not installed and nothing answers on port 11434 (see ADR-0010's local-model
decision -- the setup it describes has not been done on this box). Every value below
was written by a person, not produced by `OllamaClient.chat`. Do not treat this file
as evidence that the extraction prompt in `services/extract.py` produces usable
output from a real model -- it is evidence only that the *validation and wiring*
around a well-formed response work. Once Ollama is available, replace this fixture
(or add alongside it) with an actually recorded response and compare the two shapes;
agreement then means something this file cannot claim on its own.

Most of the chunk text below is the real text of NCD 240.4's "B. Nationally Covered
Indications", fetched 2026-08-16 from `api.coverage.cms.gov` (see
`services/policy/tests/fixtures/ncd-226.json` and `.workspace/STATE.md`). `HITS`
reuses chunk id 58 for the AHI/RDI paragraph because that is the id the real ingest
pipeline assigned it, per STATE.md ("`POST /search` for the AHI criterion ... returns
chunk 58 ... cross-encoder score 7.00"); chunk ids 56 and 57 for the "benefit from
CPAP" sentence and the diagnosis paragraph (both of which precede chunk 58 in the
real document) are invented but plausible neighbours (ingest chunk ids are
sequential per policy) -- not verified against a live `policy` database.

**Chunk 59 is different and is flagged separately below**: the NCD 240.4 text this
project has ingested does not state the numeric continuation-adherence rule ("greater
than or equal to 4 hours a night on 70% of nights in a 30-day window" --
`docs/plans/2026-08-16-03-member-service.md`) -- that number comes from the companion
LCD, which is not in this repository's local corpus. Chunk 59's text is this
project's own paraphrase of that rule, not a real ingested chunk, and is marked as
such in its own comment below. Fix round 1 added it (and set 4) specifically to
exercise `adherence_fraction`'s `fact_args` requirement (`domain/params.py`) with the
real number this project already committed to elsewhere, rather than a value invented
for the test alone.

`RAW_RESPONSE` is shaped as if it were the parsed body of an Ollama `/api/chat`
response (see `services/llm.py`) -- i.e. what `extract.extract` receives after
`llm.chat` has already decoded the JSON string in `message.content`. Sets 1-3 follow
ADR-0011's own worked example for NCD 240.4's initial-authorisation path: one set for
AHI >= 15, and two more for the AHI 5-14 band paired with either documented symptoms
(a `judgment` criterion -- interpreting clinical narrative is exactly what ADR-0003
reserves for the model) or a documented comorbidity (an `enum` criterion over
`condition_codes`, because comorbidity presence is a fact a code list settles
deterministically). Set 4 is the continuation path: documented benefit (`judgment`,
citing the real "who benefit from CPAP" sentence) plus the adherence bar itself, which
is a `threshold` criterion over `adherence_fraction` -- deterministic, per ADR-0003,
because "at least 70% of nights" is an exact question `MemberClient.adherence`
already answers, not a narrative one.

In the real pipeline, an initial-authorization case and a continuation case
(`CaseRequest.kind`) are almost certainly separate extraction calls over separate
policy chunks, not one call returning all four sets at once -- set 4 is included here
purely so one fixture can exercise the full params contract, not as a claim about how
the pipeline will call `extract`.

Deliberately simplified relative to the real NCD text, so a reviewer does not mistake
this for a complete extraction of the policy:

- The Type IV-specific "at least 3 channels" proviso is not encoded as a separate
  `threshold` criterion here (it would need to apply conditionally, only when
  `test_type == home_type_iv`, which is a shape this fixture does not exercise).
- "Coverage active" is not included as a criterion. The real indications_limitations
  text quoted above never states it -- Medicare eligibility is a precondition of the
  case, checked by the pipeline's separate eligibility stage (see CLAUDE.md's case
  flow: normalize -> eligibility -> find governing policy -> decompose into criteria
  -> ...), not something this extraction step should invent a citation for.
"""

from pramana_common.schemas import Hit

BENEFIT_TEXT = (
    "Coverage of CPAP is initially limited to a 12-week period to identify "
    "beneficiaries diagnosed with OSA as subsequently described who benefit from "
    "CPAP. CPAP is subsequently covered only for those beneficiaries diagnosed with "
    "OSA who benefit from CPAP during this 12-week period."
)

DIAGNOSIS_TEXT = (
    "A positive diagnosis of OSA for the coverage of CPAP must include a clinical "
    "evaluation and a positive: attended PSG performed in a sleep laboratory; or "
    "unattended HST with a Type II home sleep monitoring device; or unattended HST "
    "with a Type III home sleep monitoring device; or unattended HST with a Type IV "
    "home sleep monitoring device that measures at least 3 channels."
)

AHI_TEXT = (
    "An initial 12-week period of CPAP is covered in adult patients with OSA if "
    "either of the following criterion using the AHI or RDI are met: AHI or RDI "
    "greater than or equal to 15 events per hour, or AHI or RDI greater than or "
    "equal to 5 events and less than or equal to 14 events per hour with documented "
    "symptoms of excessive daytime sleepiness, impaired cognition, mood disorders or "
    "insomnia, or documented hypertension, ischemic heart disease, or history of "
    "stroke."
)

# NOT real ingested corpus text -- see the module docstring. This paraphrases the
# companion LCD's adherence definition as this project has already committed to it
# in docs/plans/2026-08-16-03-member-service.md, rather than inventing a number.
ADHERENCE_TEXT = (
    "Continued coverage of CPAP beyond the initial 12-week period requires evidence "
    "of adherence to therapy, defined as use of CPAP for greater than or equal to 4 "
    "hours per night on at least 70% of nights during a consecutive 30-day period."
)

HEADING = "Indications and Limitations of Coverage > B. Nationally Covered Indications"
CONTINUATION_HEADING = HEADING + " (adherence rule, not in the ingested NCD 240.4 corpus)"

HITS = [
    Hit(
        chunk_id=56,
        policy_id=1,
        display_id="240.4",
        heading_path=HEADING,
        text=BENEFIT_TEXT,
        score=5.1,
    ),
    Hit(
        chunk_id=57,
        policy_id=1,
        display_id="240.4",
        heading_path=HEADING,
        text=DIAGNOSIS_TEXT,
        score=6.2,
    ),
    Hit(
        chunk_id=58,
        policy_id=1,
        display_id="240.4",
        heading_path=HEADING,
        text=AHI_TEXT,
        score=7.0,
    ),
    Hit(
        chunk_id=59,
        policy_id=1,
        display_id="240.4",
        heading_path=CONTINUATION_HEADING,
        text=ADHERENCE_TEXT,
        score=4.4,
    ),
]

#: The four sleep-test modalities the diagnosis paragraph accepts, in
#: `member_client`'s `SleepStudy.test_type` vocabulary.
VALID_TEST_TYPES = ["psg", "home_type_ii", "home_type_iii", "home_type_iv"]

_DIAGNOSIS_CRITERION = {
    "text": "Diagnosis includes a clinical evaluation and a positive PSG or Type II/III/IV HST",
    "type": "enum",
    "params": {"fact": "test_type", "allowed": VALID_TEST_TYPES},
    "source_chunk_id": 57,
}

RAW_RESPONSE = {
    "sets": [
        {
            "criteria": [
                _DIAGNOSIS_CRITERION,
                {
                    "text": "AHI or RDI greater than or equal to 15 events per hour",
                    "type": "threshold",
                    "params": {"fact": "ahi", "operator": ">=", "value": 15},
                    "source_chunk_id": 58,
                },
            ]
        },
        {
            "criteria": [
                _DIAGNOSIS_CRITERION,
                {
                    "text": "AHI or RDI greater than or equal to 5 events per hour",
                    "type": "threshold",
                    "params": {"fact": "ahi", "operator": ">=", "value": 5},
                    "source_chunk_id": 58,
                },
                {
                    "text": "AHI or RDI less than or equal to 14 events per hour",
                    "type": "threshold",
                    "params": {"fact": "ahi", "operator": "<=", "value": 14},
                    "source_chunk_id": 58,
                },
                {
                    "text": (
                        "Documented symptoms of excessive daytime sleepiness, impaired "
                        "cognition, mood disorders, or insomnia"
                    ),
                    "type": "judgment",
                    "params": {},
                    "source_chunk_id": 58,
                },
            ]
        },
        {
            "criteria": [
                _DIAGNOSIS_CRITERION,
                {
                    "text": "AHI or RDI greater than or equal to 5 events per hour",
                    "type": "threshold",
                    "params": {"fact": "ahi", "operator": ">=", "value": 5},
                    "source_chunk_id": 58,
                },
                {
                    "text": "AHI or RDI less than or equal to 14 events per hour",
                    "type": "threshold",
                    "params": {"fact": "ahi", "operator": "<=", "value": 14},
                    "source_chunk_id": 58,
                },
                {
                    "text": "Documented hypertension, ischemic heart disease, or history of stroke",
                    "type": "enum",
                    "params": {
                        "fact": "condition_codes",
                        # SNOMED CT codes: hypertension, coronary artery disease
                        # (stands in for "ischemic heart disease"), cerebrovascular
                        # accident -- illustrative, not verified against a coded
                        # value set.
                        "allowed": ["59621000", "53741008", "230690007"],
                    },
                    "source_chunk_id": 58,
                },
            ]
        },
        {
            # The continuation path -- see the module docstring for why it lives
            # alongside the initial-authorisation sets in this one fixture.
            "criteria": [
                {
                    "text": "Documented benefit from CPAP during the initial 12-week period",
                    "type": "judgment",
                    "params": {},
                    "source_chunk_id": 56,
                },
                {
                    "text": (
                        "CPAP used at least 4 hours per night on at least 70% of "
                        "nights during a 30-day period"
                    ),
                    "type": "threshold",
                    "params": {
                        "fact": "adherence_fraction",
                        "operator": ">=",
                        "value": 0.70,
                        "fact_args": {"min_hours": 4.0, "window_days": 30},
                    },
                    "source_chunk_id": 59,
                },
            ]
        },
    ]
}
