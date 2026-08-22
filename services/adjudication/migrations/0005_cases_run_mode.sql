-- The signature ablation, as a column on the case rather than a second pipeline (ADR-0021).
--
-- `evals.eval_runs.ablation` has carried 'none' | 'model_arithmetic' since that service was
-- built, and the run it names could not be executed: adjudication had no mode in which the
-- model performs the comparisons deterministic code otherwise does, so POST /eval-runs
-- answered 501 rather than publish a figure labelled "model arithmetic" that SQL produced.
-- This column is the missing half.
--
-- On the case, not on the request: the worker receives an id off a Redis stream and nothing
-- else, so the mode has to be readable from the row or the worker would need a second
-- channel carrying it -- and a mode that travelled beside the case instead of on it could
-- disagree with what the audit trail says was run.
--
-- NOT NULL with a default, so every case that predates this migration reads as what it
-- actually was. A nullable column would make "this case was adjudicated by deterministic
-- code" and "nobody recorded how this case was adjudicated" the same value, on the one
-- column that says whether a determination came from the system as designed or from the
-- experiment that exists to argue against it.
--
-- The vocabulary is 'deterministic' | 'model_arithmetic', not eval_runs' 'none' |
-- 'model_arithmetic': a case's run_mode is a description of how it was decided, and
-- `run_mode = 'none'` would read as "no mode". The mapping between the two lives in exactly
-- one place, `evals/services/runner.py`, and is one conditional.
ALTER TABLE cases
    ADD COLUMN run_mode text NOT NULL DEFAULT 'deterministic'
        CHECK (run_mode IN ('deterministic', 'model_arithmetic'));
