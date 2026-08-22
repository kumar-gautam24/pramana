-- One case, one determination -- enforced by the database rather than by the application.
--
-- `pipeline.adjudicate` grew a re-entry guard on 2026-08-22 (it returns the committed
-- determination instead of re-deriving the case), and that guard fixed the harm it was
-- written for: a determination whose `blocking` criterion ids had been erased by the
-- delete-then-insert in `criteria.insert_many`. It did not stop a case acquiring two
-- determinations, because it is a check-then-act read and two pipeline runs for the same
-- case were observed overlapping -- one process, one consumer, one stream entry, and two
-- full `_process_one` invocations whose `started` events interleave 1-3 seconds apart. The
-- mechanism producing that second run was not identified, which is precisely the argument
-- for constraining the outcome instead of the cause: a unique index holds whatever the
-- cause turns out to be, and holds it in the one place no application path can go around.
--
-- Why this is worth a schema change rather than a carried finding. `evals.runner`'s
-- `_decision_from` takes the *first* `decision` event a case emitted. When a case reaches
-- the gate on one run and exhausts its retry ladder on the other, the two decisions land
-- milliseconds apart in an order nothing controls, so the harness scores whichever
-- happened to be written first. Measured 2026-08-22 on case dc06c6d6: an
-- `upstream_unavailable` escalation at 12:22:05.345 and the real gate escalation (blocking
-- criterion 194) at 12:22:05.670. The harness would have scored the first. That is not a
-- retrieval or a reasoning result -- it is a race deciding what the eval measures, and no
-- number computed over it means anything.

-- Refuse rather than choose. A determination is the record of what this system decided
-- about a member's request; picking which of two survives is a clinical-record judgment and
-- migration 0004 already set the precedent that a migration does not make one. The rule a
-- human should apply when resolving these by hand is stated here rather than left implicit:
-- keep the determination whose evidence still exists. `criteria.insert_many`
-- delete-then-inserts, so only the *last* run's criteria rows remain in the table -- which
-- means a determination from an earlier run may cite ids that resolve to nothing, and the
-- last one written is the only one guaranteed to be consistent with what a reviewer can
-- still read.
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(case_id::text, ', ' ORDER BY case_id::text)
      INTO offending
      FROM (SELECT case_id FROM determinations GROUP BY case_id HAVING count(*) > 1) dupes;

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'these cases hold more than one determination: %. Each is a recorded '
            'determination about a member''s request and this migration will not choose '
            'between them; keep the one whose blocking criteria still resolve (normally '
            'the last written) and remove the rest by hand, then re-run.',
            offending;
    END IF;
END $$;

-- A plain UNIQUE constraint rather than a partial index: there is no determination shape
-- that is exempt. The four short-circuits, the exhausted retry ladder and an ordinary gate
-- decision are all the case's one answer, and a case that has any of them is finished.
ALTER TABLE determinations
    ADD CONSTRAINT determinations_one_per_case UNIQUE (case_id);
