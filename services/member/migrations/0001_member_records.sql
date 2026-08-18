-- Transcribed by hand from the retired Alembic revision 0001 (see ADR-0013). No
-- autogenerate involved this time, which is the point -- see the equivalent note in
-- services/policy/migrations/0001_policies_and_chunks.sql.

CREATE TABLE members (
    id varchar(64) PRIMARY KEY,
    birth_date date NOT NULL,
    sex varchar(1) NOT NULL,
    coverage_start date NOT NULL,
    -- NULL means open-ended coverage. A far-future sentinel date would silently expire
    -- a member's coverage on that day.
    coverage_end date
);

CREATE TABLE conditions (
    id serial PRIMARY KEY,
    member_id varchar(64) NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    -- SNOMED. Criteria match on codes, not prose.
    code varchar(32) NOT NULL,
    description text NOT NULL,
    onset_date date NOT NULL
);

CREATE INDEX ix_conditions_member_id ON conditions (member_id);

CREATE TABLE cpap_usage (
    id serial PRIMARY KEY,
    member_id varchar(64) NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    night date NOT NULL,
    hours double precision NOT NULL,
    -- Adherence is a count of qualifying nights. A duplicate night would inflate that
    -- count and approve a member who did not meet the 70%-of-30-nights threshold.
    CONSTRAINT uq_cpap_usage_member_night UNIQUE (member_id, night)
);

CREATE INDEX ix_cpap_usage_member_id ON cpap_usage (member_id);

CREATE TABLE encounters (
    id serial PRIMARY KEY,
    member_id varchar(64) NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    date date NOT NULL,
    description text NOT NULL
);

CREATE INDEX ix_encounters_member_id ON encounters (member_id);

CREATE TABLE sleep_studies (
    id serial PRIMARY KEY,
    member_id varchar(64) NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    date date NOT NULL,
    -- Attended PSG or a Type II/III/IV home study; which one governs which channel
    -- threshold applies.
    test_type varchar(32) NOT NULL,
    -- Type IV needs at least 3 channels -- the policy's own cutoff.
    channels integer NOT NULL,
    -- Stored alongside the derived index, not instead of it: NCD 240.4 states its
    -- criteria both as a raw event/hour count and as AHI, and only keeping AHI would
    -- make the first form unanswerable.
    apnea_events integer NOT NULL,
    recorded_hours double precision NOT NULL,
    ahi double precision NOT NULL
);

CREATE INDEX ix_sleep_studies_member_id ON sleep_studies (member_id);

CREATE TABLE notes (
    id serial PRIMARY KEY,
    member_id varchar(64) NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    -- Nullable: not every note is tied to a specific encounter.
    encounter_id integer REFERENCES encounters (id) ON DELETE CASCADE,
    date date NOT NULL,
    -- What the judgment criteria read.
    text text NOT NULL
);

CREATE INDEX ix_notes_encounter_id ON notes (encounter_id);
CREATE INDEX ix_notes_member_id ON notes (member_id);
