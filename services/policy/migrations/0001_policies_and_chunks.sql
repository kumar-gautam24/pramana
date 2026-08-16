-- Transcribed from the retired Alembic revision b65e4d6e31a6 (see ADR-0013). No
-- autogenerate involved this time, which is the point: autogenerate produced this
-- extension statement missing entirely on the original run, caught only by review.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE policies (
    id serial PRIMARY KEY,
    document_id varchar(32) NOT NULL,
    document_version integer NOT NULL,
    -- The number a human cites, e.g. "240.4". Distinct from document_id ("226"), which
    -- is the API's internal key -- both are needed, and confusing them silently
    -- retrieves the wrong policy.
    display_id varchar(64) NOT NULL,
    title text NOT NULL,
    effective_from date NOT NULL,
    -- NULL means open-ended. The API expresses this as the literal string "N/A".
    effective_to date,
    benefit_category text NOT NULL,
    source_url text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    -- Ingest is idempotent by (document_id, document_version): a version already stored
    -- makes re-running ingest a no-op instead of a duplicate corpus.
    CONSTRAINT uq_policies_document_id_version UNIQUE (document_id, document_version)
);

CREATE INDEX ix_policies_display_id ON policies (display_id);

CREATE TABLE chunks (
    id serial PRIMARY KEY,
    policy_id integer NOT NULL REFERENCES policies (id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    heading_path text NOT NULL,
    text text NOT NULL,
    -- 384 is bge-small's output width. It is a column type, so it cannot follow the
    -- configured model at runtime: changing the embedding model means a migration and a
    -- re-embed of the whole corpus, not a settings edit.
    embedding vector(384) NOT NULL,
    -- Generated rather than populated by the application: the database is the only
    -- place that can guarantee it stays in step with `text`.
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED NOT NULL
);

CREATE INDEX ix_chunks_policy_id ON chunks (policy_id);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
