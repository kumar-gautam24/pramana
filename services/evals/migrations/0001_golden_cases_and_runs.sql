CREATE TABLE golden_cases (
    id bigserial PRIMARY KEY,
    -- The case input, exactly as it would be POSTed to adjudication: member_id,
    -- requested_code, icd10, date_of_service, kind, request_text. Held as jsonb rather
    -- than as columns because this service does not interpret it -- it forwards it --
    -- and mirroring another service's request schema here would be two definitions of
    -- one contract, drifting apart on the day adjudication adds a field.
    fixture jsonb NOT NULL,
    -- approve or escalate, and nothing else. The system has no deny path (ADR-0002), so
    -- the eval schema must not be able to express one: a golden case expecting a denial
    -- would be a test for behaviour that must never exist.
    expected_outcome text NOT NULL CHECK (expected_outcome IN ('approve', 'escalate')),
    -- The human-authored criteria list this case should decompose into, for scoring
    -- extraction precision and recall.
    expected_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Not decoration. A label a model wrote is not a label -- it measures agreement
    -- between two models, not correctness -- and ADR-0009 exists because the
    -- predecessor project learned that the easy way to build a refusal set is the way
    -- that flatters it. NOT NULL so authorship cannot be omitted and later assumed.
    author text NOT NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
    id bigserial PRIMARY KEY,
    -- Everything needed to reproduce this run. California requires AI tools used in
    -- coverage decisions to be periodically assessed for accuracy; an assessment whose
    -- conditions were not recorded cannot be repeated, and an unrepeatable measurement
    -- is not evidence.
    model text NOT NULL,
    prompt_version text NOT NULL,
    thresholds jsonb NOT NULL,
    git_sha text NOT NULL,
    -- Whether the model did the arithmetic instead of deterministic code -- the
    -- signature ablation. A run and its ablated twin differ in this column and nothing
    -- else, which is what makes the comparison an argument rather than an anecdote.
    ablation text NOT NULL DEFAULT 'none' CHECK (ablation IN ('none', 'model_arithmetic')),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE eval_results (
    id bigserial PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES eval_runs (id) ON DELETE CASCADE,
    golden_case_id bigint NOT NULL REFERENCES golden_cases (id) ON DELETE CASCADE,
    -- The adjudication case this row scored, recorded as a value only: no foreign key
    -- crosses a service boundary.
    case_id uuid,
    -- Nullable, unlike golden_cases.expected_outcome: a case that never got a
    -- determination (the model was rate-limited, the upstream was down) has no outcome,
    -- and writing 'escalate' there would silently count an infrastructure failure as a
    -- correct refusal.
    outcome text CHECK (outcome IS NULL OR outcome IN ('approve', 'escalate')),
    reason text,
    criterion_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Why this row has no outcome, when it has none.
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- One result per case per run: a retried case must update its row rather than add a
    -- second, or the denominator of every rate below silently grows.
    CONSTRAINT uq_eval_results_run_case UNIQUE (run_id, golden_case_id)
);

CREATE INDEX ix_eval_results_run_id ON eval_results (run_id);
