# member

**The member's clinical record, as deterministic fact endpoints.** Port 8005, database
`pramana_member`.

Every fact the adjudication pipeline compares against a policy criterion comes from here, and
every one of them is a SQL query rather than a model call. That division is the project's thesis:
the model decides what the rules are, this service supplies the facts they point at.

## Data model

| table | holds |
| --- | --- |
| `members` | the member, with coverage status and effective dates |
| `conditions` | diagnosis codes (SNOMED) with onset dates |
| `sleep_studies` | test type, channel count, apnea events, recorded hours, study date |
| `cpap_usage` | one row per night: hours used |
| `notes` | clinical narrative — initial visit and continuation follow-up |
| `encounters` | visits, carried from the Synthea substrate |

The population is synthetic: [Synthea](https://synthetichealth.github.io/synthea/) provides the
demographic and encounter substrate, and a sleep-medicine layer is generated on top of it,
because Synthea does not model apnea event counts or CPAP adherence.

## The endpoints are answers, not tables

| method | path | answers |
| --- | --- | --- |
| GET | `/members/{id}/coverage?on=DATE` | was coverage active on that date |
| GET | `/members/{id}/sleep-studies?before=DATE` | studies on or before the date of service |
| GET | `/members/{id}/conditions?codes=...` | does this member carry any of these codes |
| GET | `/members/{id}/adherence?min_hours=4&...` | fraction of nights at or above the bar |
| GET | `/members/{id}/notes?before=DATE` | narrative available at the date of service |
| POST | `/seed` | generate the fixture population |
| GET | `/health` · `/ready` | liveness, readiness |

Two design rules run through all of them:

**No policy value has a default.** `min_hours` is a *required* parameter, not one defaulting to
4.0. The 4-hour adherence bar is a number NCD 240.4 chose; if it lived here as a default, the
policy value would be hiding in `src/` where nobody auditing the policy would look for it. The
same applies to `codes` on the conditions endpoint — there is deliberately no way to ask for
"all conditions", because a criterion always names what it is looking for.

**Every date is bounded by the date of service.** A study performed after the date of service
cannot support a request made before it, so the cutoff is a parameter rather than something the
caller is trusted to filter afterwards.

## The distinction that makes verifiers safe

An empty list from this service means **the fact is absent**, not that the member is unknown.
That is only safe because the pipeline checks eligibility and short-circuits on "no record"
*before* any verifier runs. Without that ordering, a member the system has never heard of would
produce denial-shaped `NOT_MET` answers about their care.

Coverage reports three states, and the pipeline treats them differently:

| state | meaning | pipeline result |
| --- | --- | --- |
| active | covered on the date | continue |
| inactive | a record exists and says not covered | escalate, `criterion_not_met` |
| no record | nothing known about this member | escalate, `insufficient_evidence` |

A missing document and a contradicting document are not the same evidence, and collapsing them
would be the difference between "we could not tell" and "the answer is no".

## The fixture population

`POST /seed {"seed": 42}` produces 5 members, 5 sleep studies, 150 usage nights and 10 notes,
deterministically. Each member exercises a different path through NCD 240.4:

| member | test type | channels | AHI | qualifies on | note |
| --- | --- | --- | --- | --- | --- |
| `p1` | home type IV | 4 | 46.916 | AHI alone | hypertension on record |
| `p2` | home type II | 6 | 14.446 | **nothing** | the deliberate near-miss |
| `p3` | home type III | 3 | 8.902 | mild band only | one documented symptom |
| `p4` | home type IV | 4 | 19.171 | AHI alone | closest passing case |
| `p5` | home type IV | **2** | 47.964 | fails study validity | one channel short |

`p2` is the case the eval set exists for. Its AHI of 14.446 is below the threshold of 15 **and**
above the 5–14 band's ceiling, so it qualifies under neither alternative criteria set — while
carrying ischemic heart disease, which *is* a qualifying comorbidity at AHI 5–14. For a period
this population's near-miss band started at exactly 14.0 and that value was reachable, making
`p2` intermittently a legitimate approval. A near-miss that is only near-miss by accident
measures nothing, which is why the population has its own invariant tests.

Adherence at `min_hours=4`: p1 0.80, p2 0.60, p3 0.87, p4 0.90, p5 0.83.

## Running and testing

```bash
cd services/member
uv run python ../../scripts/migrate.py "postgresql://pramana:pramana@localhost:5432/pramana_member" migrations
uv run uvicorn member.main:app --port 8005
uv run pytest        # 91 tests
```

To regenerate the population from scratch, delete the members first — five foreign keys cascade —
then seed again:

```sql
DELETE FROM members;
```

**Verify the artifact, not the exit code.** A `/seed` call against a stale container image
returns a success message and a population missing whatever the newer generator added. The check
that matters is querying for the thing you expect to be there.

## Caveats

- **One test fails on a clean checkout for environmental reasons.** `test_health` constructs
  `Settings` without `DATABASE_URL` in the environment and fails validation. It passes with the
  variable set; not a logic failure.
- **The generator encodes policy-shaped numbers** — adherent nights at 0.75–0.95 against a 70%
  bar, nightly hours uniform over 4–8 against a 4-hour bar. Defensible because the generator is
  test apparatus and near-misses have to be aimed at the real thresholds, but it is the same
  "policy value in `src/`" shape that was deliberately removed from the query layer. Revisit if a
  second policy is ever evaluated.
- **A member row with no children is skipped forever by the seeder**, which checks only for the
  member id. Not currently reachable because `/seed` commits once at the end.
- **Reaching sixty golden cases needs a larger member plan**, not a signature change — the seeder
  already takes its population as an argument, and only the route is bound to the 5-row fixture.
