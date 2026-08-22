-- Closes the one column plan 04 shipped deliberately open. Migration 0001's comment on
-- `reviews.outcome` says why it was left unconstrained -- a licensed clinician may issue an
-- adverse determination and the machine may not, so this column cannot borrow
-- `determinations.outcome`'s two-value CHECK -- and hands the vocabulary to plan 07 as a
-- regulatory question. ADR-0019 answers it: approve, deny, pend.
--
-- Not approve/deny alone: a clinician who cannot decide from the record needs a disposition
-- that is not a denial, or the case leaves the flywheel unrecorded (`agreed_with_system` is
-- how clinical work becomes eval data, and it only exists on a row).
--
-- Not partial approval, and that exclusion is the considered part. A Pramana case carries one
-- requested_code, one date_of_service, and no quantity, duration or units of service, so a
-- partial approval has nothing here to be partial of. Worse, a partial approval is legally an
-- adverse determination as to the portion refused -- recorded as a fourth flat value it would
-- hide the fact that a denial occurred, which is exactly the fact Illinois law is about. See
-- ADR-0019 for the condition under which this reopens.

-- The console has proposed `approve` / `deny` / `more_information` at the point a review is
-- authored since plan 07, so that is the only non-vocabulary value that can exist. It maps to
-- `pend`, which is the disposition's name in utilization-management practice and in the state
-- statutes that regulate it.
UPDATE reviews SET outcome = 'pend' WHERE outcome = 'more_information';

-- Anything still outside the set stops this migration with the offending values named, rather
-- than surfacing as a bare check_violation naming only the constraint. It is deliberately not
-- coerced to a default: a row here is a licensed clinician's recorded determination on a real
-- case, and a migration that silently rewrote one would be falsifying the record the whole
-- table exists to keep. A human decides what such a row meant.
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(DISTINCT quote_literal(outcome), ', ')
      INTO offending
      FROM reviews
     WHERE outcome NOT IN ('approve', 'deny', 'pend');

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'reviews.outcome holds % outside the vocabulary ADR-0019 closes it to '
            '(approve, deny, pend). These are recorded clinician determinations and this '
            'migration will not rewrite them; resolve each row by hand and re-run.',
            offending;
    END IF;
END $$;

ALTER TABLE reviews
    ADD CONSTRAINT reviews_outcome_check CHECK (outcome IN ('approve', 'deny', 'pend'));
