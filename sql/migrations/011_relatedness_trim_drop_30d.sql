-- MANUAL
-- ^ deploy.sh must skip-and-warn on this file, never auto-apply it (§7.6 convention).
--   This migration contains a DELETE — "idempotent" is not "non-destructive", and a
--   boot-time glob of migrations/*.sql must not re-run a delete unattended. Marker is
--   the mechanical guard; apply this by hand via the SSM tunnel on the inauguration
--   day (or once against the existing dev DB before the AWS move).
--
-- AWS-migration relatedness trim (§7.3). CORRELATION_WINDOWS dropped the 30d window
-- (kept 90d + 365d) and RELATEDNESS_MIN_R was raised 0.20 → 0.50, to cut ~20M weekly
-- rows ahead of the shared-RDS move. The nightly compute now only writes 90d/365d and
-- only pairs with |r| >= 0.50, but existing rows from prior runs (30d pairs, and
-- 90d/365d pairs with 0.20 <= |r| < 0.50) are orphaned and must be deleted once.
--
-- Behavior-neutral: relatedness_matrix has no wired production reader as of 2026-07-08
-- (the LLM peer-lookup consumer is designed but not built — see config.py and the
-- roadmap "wire or retire" decision item). Nothing consumes the rows this deletes.
--
-- Applied: MANUAL — pending (run once on the inauguration day / pre-move dev DB)
-- Idempotent: safe to re-run (the nightly DAG re-establishes 90d/365d @ |r|>=0.50).

-- Drop the retired 30d window entirely.
DELETE FROM relatedness_matrix
    WHERE window_days = 30;

-- Drop sub-threshold pairs left by the old 0.20 floor, for the windows we keep.
DELETE FROM relatedness_matrix
    WHERE window_days IN (90, 365)
      AND ABS(pearson_r) < 0.50;
