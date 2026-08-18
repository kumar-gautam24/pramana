-- gen_random_uuid() is built into Postgres 16 (pgcrypto folded into core); no
-- extension needed, unlike policy's pgvector.

CREATE TABLE cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- member's own primary key, recorded as a value only: no foreign key crosses a
    -- service boundary (member owns its database, adjudication owns this one).
    member_id varchar(64) NOT NULL,
    -- CPT code, identifier only -- the description text carries an AMA licence and is
    -- never stored here. See ADR-0004.
    requested_code varchar(16) NOT NULL,
    icd10 varchar(16) NOT NULL,
    date_of_service date NOT NULL,
    kind text NOT NULL CHECK (kind IN ('initial', 'continuation')),
    -- Pipeline progress only. The outcome lives on determinations and is never
    -- mirrored here -- two copies of a decision drift, and only one of them is the
    -- one a regulator reads.
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'decided', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE criteria (
    id bigserial PRIMARY KEY,
    case_id uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    -- Criteria sharing a set_ordinal belong to the same alternative set in the
    -- extracted disjunctive-normal-form policy -- see ADR-0011. The gate approves if
    -- every criterion in any one set is met.
    set_ordinal integer NOT NULL,
    -- Position within the set.
    ordinal integer NOT NULL,
    text text NOT NULL,
    type text NOT NULL CHECK (type IN ('threshold', 'enum', 'temporal', 'judgment')),
    params jsonb NOT NULL,
    -- policy's chunk id and human-readable citation, recorded as values only: no
    -- foreign key crosses a service boundary.
    source_chunk_id integer NOT NULL,
    source_display_id varchar(64) NOT NULL,
    -- Two criteria cannot occupy the same position in the same alternative set.
    CONSTRAINT uq_criteria_case_set_ordinal UNIQUE (case_id, set_ordinal, ordinal)
);

CREATE TABLE criterion_results (
    id bigserial PRIMARY KEY,
    criterion_id bigint NOT NULL REFERENCES criteria (id) ON DELETE CASCADE,
    verdict text NOT NULL CHECK (verdict IN ('met', 'not_met', 'insufficient_evidence')),
    confidence double precision NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    -- Which deterministic tool (or, for judgment criteria, which model call) produced
    -- this verdict. Free text: the set of tools grows without a migration.
    tool text NOT NULL,
    evidence jsonb NOT NULL
);

CREATE INDEX ix_criterion_results_criterion_id ON criterion_results (criterion_id);

CREATE TABLE determinations (
    id bigserial PRIMARY KEY,
    case_id uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    -- The database must be structurally incapable of holding a denial: this is
    -- ADR-0002 enforced at the storage layer, not merely by application code that a
    -- future change could bypass. A licensed clinician's adverse determination lives
    -- in reviews, never here.
    outcome text NOT NULL CHECK (outcome IN ('approve', 'escalate')),
    -- Why the gate declined to approve. NULL on approve; one of GateReason's closed
    -- set on escalate.
    reason text CHECK (reason IS NULL OR reason IN (
        'no_criteria', 'criterion_not_met', 'insufficient_evidence', 'low_confidence'
    )),
    blocking jsonb NOT NULL,
    thresholds jsonb NOT NULL,
    -- NULL on escalation. On approval, names the satisfied set (criteria.set_ordinal)
    -- -- the audit answer to "which path approved this".
    winning_set integer,
    created_at timestamptz NOT NULL DEFAULT now()
    -- No unique constraint on case_id: a case may be adjudicated more than once, and a
    -- superseded determination must survive. The current one is the newest by
    -- created_at, then id.
);

CREATE INDEX ix_determinations_case_id ON determinations (case_id);

CREATE TABLE reviews (
    id bigserial PRIMARY KEY,
    case_id uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    -- auth's own primary key, recorded as a value only: no foreign key crosses a
    -- service boundary.
    clinician_id text NOT NULL,
    -- Unlike determinations.outcome, not constrained to approve/escalate: a licensed
    -- clinician may issue an adverse determination here (California SB 1120, the
    -- Medicare Advantage rule) -- that is the entire reason this table is separate
    -- from determinations.
    outcome text NOT NULL,
    rationale text NOT NULL,
    agreed_with_system boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_reviews_case_id ON reviews (case_id);

CREATE TABLE case_events (
    id bigserial PRIMARY KEY,
    -- RESTRICT, not CASCADE like every other child table here: a cascading delete
    -- would reach this table and trip the append-only trigger below anyway, so the
    -- delete fails either way. RESTRICT says the true thing directly -- a case whose
    -- audit trail exists cannot be deleted -- instead of surfacing that fact as a
    -- trigger error several layers down.
    case_id uuid NOT NULL REFERENCES cases (id) ON DELETE RESTRICT,
    -- Per-case and assigned by the writer, not a global sequence -- what makes
    -- UNIQUE (case_id, seq) meaningful: a gap or a duplicate is a constraint
    -- violation rather than a silent reordering.
    seq integer NOT NULL,
    type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_case_events_case_seq UNIQUE (case_id, seq)
);

-- Append-only, enforced by the database rather than by convention: a commissioner's
-- audit rests on it (ADR-0005). One function serves both triggers below; TG_OP
-- reads 'UPDATE', 'DELETE' or 'TRUNCATE' depending on which fired it.
CREATE FUNCTION case_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'case_events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER case_events_no_update_delete
    BEFORE UPDATE OR DELETE ON case_events
    FOR EACH ROW EXECUTE FUNCTION case_events_append_only();

-- TRUNCATE bypasses row-level triggers entirely, so the trigger above cannot see it.
-- An audit log a TRUNCATE could empty is not an audit log.
CREATE TRIGGER case_events_no_truncate
    BEFORE TRUNCATE ON case_events
    FOR EACH STATEMENT EXECUTE FUNCTION case_events_append_only();
