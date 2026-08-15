-- One database per service. Run once by the Postgres image's init hook.
-- pgvector is enabled only on the policy database: it is the only service storing
-- embeddings, and an extension granted where it is not needed is surface for nothing.
CREATE DATABASE pramana_policy;
CREATE DATABASE pramana_adjudication;
CREATE DATABASE pramana_evals;
CREATE DATABASE pramana_auth;
CREATE DATABASE pramana_member;

\connect pramana_policy
CREATE EXTENSION IF NOT EXISTS vector;
