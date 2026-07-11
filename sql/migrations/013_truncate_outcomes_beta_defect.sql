-- MANUAL
-- 013_truncate_outcomes_beta_defect.sql
--
-- DEFECT #12 (plan of record 1.3.8): the inauguration §8 chain ran
-- backfill_outcomes_v2 BEFORE relatedness, but relatedness
-- (compute_and_upsert_betas) is what populates sector_beta. With sector_beta
-- empty, every ticker took the documented beta_hat=1.0 fallback
-- (outcome_tracker/tasks.py:373) and Vasicek-shrank 1.0 -> 1.0, so all
-- 22,960,944 rows carry beta_used = 1.0000 exactly. excess_return (raw - beta*bench)
-- is therefore computed on a placeholder beta and is not the final metric.
--
-- WHY TRUNCATE, NOT A BARE RE-RUN: resolve_outcomes_v2 is RESOLVE-ONCE
-- (verified at code level, tasks.py:318-323 load filter + 402-403 per-horizon
-- skip). A re-run after relatedness would NOT re-touch the 22.46M already-resolved
-- rows — it would only resolve the ~501k unresolved tail on real beta, leaving a
-- corrupted ~98%/2% mix that could false-pass a naive median re-gate. The table
-- must be emptied so the re-run resolves the whole population against real betas.
--
-- SEQUENCE (INFRA executes, SIGNAL gates): this migration -> run relatedness
-- (populates sector_beta [90,365]) -> clean full backfill_outcomes_v2 -> SIGNAL
-- re-gate (beta median 0.8-1.2, p1/p99 in [-1,3], COUNT(DISTINCT beta_used) > 1).
--
-- prediction_outcomes has no FK children; TRUNCATE is a clean full reset and
-- reclaims space immediately. The table is fully reproducible from
-- signal_predictions + raw_prices + sector_beta, so this is loss-free.

-- Pre-check: record what we are about to drop (expect ~22,960,944).
DO $$
DECLARE
    n bigint;
    n_beta1 bigint;
BEGIN
    SELECT COUNT(*) INTO n FROM prediction_outcomes;
    SELECT COUNT(*) INTO n_beta1 FROM prediction_outcomes WHERE beta_used = 1.0;
    RAISE NOTICE 'PRE  prediction_outcomes rows=%, of which beta_used=1.0 exactly: %', n, n_beta1;
END $$;

TRUNCATE TABLE prediction_outcomes;

-- Post-check: table is empty; ready for a clean re-run on real betas.
DO $$
DECLARE
    n bigint;
BEGIN
    SELECT COUNT(*) INTO n FROM prediction_outcomes;
    IF n <> 0 THEN
        RAISE EXCEPTION 'POST prediction_outcomes still has % rows after TRUNCATE', n;
    END IF;
    RAISE NOTICE 'POST prediction_outcomes rows=0 (clean). Now: relatedness -> backfill_outcomes_v2 -> re-gate.';
END $$;
