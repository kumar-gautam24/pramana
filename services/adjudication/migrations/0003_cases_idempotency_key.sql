-- Task 8's POST /cases must be idempotent on a caller-supplied key so a retried
-- submission (the normal shape of an at-least-once client) does not enqueue -- and
-- therefore adjudicate -- the same case twice. A UNIQUE constraint, not a read-then-
-- insert check in application code: two concurrent retries racing a read-then-insert
-- can both pass the read before either commits, and only a database constraint closes
-- that window. services/intake.py's insert-and-catch-the-violation is what turns this
-- constraint into "return the existing case" instead of a second row.
--
-- Nullable, and a plain UNIQUE constraint rather than a partial index: Postgres already
-- treats every NULL as distinct under UNIQUE, so a caller with no idempotency concern of
-- its own -- every existing case, and any caller that never retries -- can omit the key
-- on every request without ever colliding with another omitted key.
ALTER TABLE cases ADD COLUMN idempotency_key text;
ALTER TABLE cases ADD CONSTRAINT uq_cases_idempotency_key UNIQUE (idempotency_key);
