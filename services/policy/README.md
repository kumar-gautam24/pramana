# policy

**Turns coverage determinations into retrievable, effective-dated passages.** Port 8001,
database `pramana_policy`.

This service answers one question for the pipeline: *given this clinical narrative and this date
of service, which passages of which policy govern?* Everything else it does exists to make that
answer trustworthy.

## Data model

| table | holds |
| --- | --- |
| `policies` | one row per policy **version**, with its effective date range and display id (e.g. `240.4`) |
| `chunks` | heading-aware passages of a policy version, with an embedding and the section they came from |

A policy is versioned, not overwritten. NCD 240.4 has been revised; a case dated 2008 and a case
dated 2026 must be judged against different text, and both texts stay in the table.

## Ingest

```
CMS Coverage API ──► unescape ──► heading-aware chunk ──► embed ──► store
```

`POST /ingest {"ncd_id": 226}` fetches `GET /v1/data/ncd?ncdid=226` from the CMS Coverage API.
No API key and no licence token are needed for the JSON endpoint. Criteria live in the
`indications_limitations` field.

Two properties of that payload are easy to get wrong and are handled explicitly:

- **The HTML is doubly escaped.** It needs `html.unescape` twice. Once leaves `&lt;strong&gt;`
  in the text.
- **Headings are `<strong>` tags, not `<h1>`–`<h6>`.** A generic chunker finds zero headings and
  emits one enormous chunk, which destroys retrieval and looks like it worked.

The whole batch commits in one transaction: a failure partway through must not leave a policy
without its chunks, or some records ingested and others silently dropped.

**Content conservation is verified, not assumed.** On this corpus: 469 source tokens in, 469
stored, zero missing. A chunker that drops the prose following a heading is a defect this project
has actually shipped, and it was invisible to every unit test because no fixture had prose in
that position.

## Retrieval

`POST /search {"query": "...", "date_of_service": "2026-01-15", "limit": 5}`

```
resolve governing version(s) for the date  ──►  hybrid retrieve  ──►  rerank  ──►  top N
        (over the full policies table)          vector + lexical     cross-encoder
```

- **Effective-dating is resolved first**, over the full `policies` table, *before* retrieval.
  Filtering afterwards — or re-deriving the governing version from the returned hits — lets a
  superseded policy govern a case. That bug has shipped twice here, the second time through a
  grouping key, so the resolution happens in exactly one place.
- **Hybrid retrieval**: vector similarity from `BAAI/bge-small-en-v1.5` fused with lexical
  matching, because policy text is full of exact phrases ("at least 4 hours per night") that
  embeddings blur.
- **Reranking** by the cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2`.

**The rerank score is an unbounded logit, not a probability.** Observed between 0.56 and 7.00 on
this corpus. Any threshold built on it must be calibrated to that scale, and bounding the field
to `[0, 1]` later would be a silent breaking change for every consumer.

`limit` is bounded at both ends. A negative limit slices the ranked list from the end and quietly
returns nearly everything; a limit above the candidate pool promises more hits than fusion ever
produces. Both are rejected rather than answered with something else.

### Measured retrieval behaviour

For NCD 240.4 the criteria-bearing chunks are 57, 58, 59, 69 and 70.

| query | chunks returned | reranker scores |
| --- | --- | --- |
| codes only (`E0601 G47.33`) | 1 of 5, rest boilerplate | flat, around −11 |
| natural-language narrative | 4 of 5 | spread from 7.2 down |

A flat band around −11 is a cross-encoder saying *nothing here matches*. Codes are out of
distribution for a model trained on question/passage pairs — which is why a case carries its
submitted narrative and only falls back to its codes.

Live check: `POST /search` for the AHI criterion dated 2026-01-15 returns chunk 58 under
*Indications and Limitations of Coverage > B. Nationally Covered Indications*, containing
"greater than or equal to 15 events per hour", score 7.00. The same query dated 2001-01-01
returns `[]` — NCD 240.4 was not in force then.

## Endpoints

| method | path | purpose |
| --- | --- | --- |
| POST | `/ingest` | fetch and store an NCD by id |
| POST | `/search` | retrieve governing passages for a query and date |
| GET | `/health` | liveness |
| GET | `/ready` | readiness, including a database round-trip |

## Configuration

| variable | default |
| --- | --- |
| `DATABASE_URL` | — |
| `CMS_BASE_URL` | the CMS Coverage API base |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` |

Both models are downloaded on first boot and cached in a volume. **That download is why the
service takes about thirty seconds to become ready**, and why `adjudication` — which probes it at
startup — cannot be started at the same moment.

## Running and testing

```bash
cd services/policy
uv run python ../../scripts/migrate.py "postgresql://pramana:pramana@localhost:5432/pramana_policy" migrations
uv run uvicorn policy.main:app --port 8001
uv run pytest        # 93 tests
```

## Caveats

- **One test fails on a clean checkout for environmental reasons.**
  `test_ready_reports_ready_when_every_dependency_answers` returns 503 when its database is not
  reachable at the DSN the test assumes. Not a logic failure.
- **`/ready` opens a pooled connection per call**, so a frequent health probe costs a round-trip
  each time.
- **`/ready`'s `except Exception` is deliberately broad** — the caller-facing collapse to
  `reason: "database"` is intentional, but it also swallows programming errors.
- **The corpus is two NCDs.** `SECTION_HEADINGS` is derived from the NCD structure; a Local
  Coverage Determination carries a different field set and would need revisiting.
