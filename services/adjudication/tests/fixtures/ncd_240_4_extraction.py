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

The chunk text below is the real text of NCD 240.4's "B. Nationally Covered
Indications", fetched 2026-08-16 from `api.coverage.cms.gov` (see
`services/policy/tests/fixtures/ncd-226.json` and `.workspace/STATE.md`). `HITS`
reuses chunk id 58 for the AHI/RDI paragraph because that is the id the real ingest
pipeline assigned it, per STATE.md ("`POST /search` for the AHI criterion ... returns
chunk 58 ... cross-encoder score 7.00"); chunk id 57 for the diagnosis paragraph is
an invented but plausible neighbour (ingest chunk ids are sequential per policy) --
not verified against a live `policy` database.

`RAW_RESPONSE` is shaped as if it were the parsed body of an Ollama `/api/chat`
response (see `services/llm.py`) -- i.e. what `extract.extract` receives after
`llm.chat` has already decoded the JSON string in `message.content`. It follows
ADR-0011's own worked example for NCD 240.4's initial-authorisation path: one set for
AHI >= 15, and two more for the AHI 5-14 band paired with either documented symptoms
(a `judgment` criterion -- interpreting clinical narrative is exactly what ADR-0003
reserves for the model) or a documented comorbidity (an `enum` criterion over
`condition_codes`, because comorbidity presence is a fact a code list settles
deterministically).

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

HEADING = "Indications and Limitations of Coverage > B. Nationally Covered Indications"

HITS = [
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
    ]
}
