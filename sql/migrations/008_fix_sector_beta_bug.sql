-- BUGFIX: sector_beta column order mismatch
-- Applied: 2026-06-10
--
-- ISSUE: _INSERT_BETAS in dag_components/relatedness/tasks.py had column order
-- (ticker, etf_proxy, beta, window_days) but beta_values() returns
-- (ticker, etf_proxy, window_days, beta). The last two were swapped, causing:
-- - Beta floats truncated into INTEGER window_days column (values become 0 or 1)
-- - window_days ints (90, 365) inserted into NUMERIC beta column
-- - ON CONFLICT collapsed rows; only 2 rows remained in the table
--
-- All prediction_outcomes computed with beta=1.0 fallback. This migration:
-- 1. Clears the corrupted table
-- 2. Column order is now fixed in tasks.py line 28
-- 3. Re-run dag_stock_relatedness to repopulate

TRUNCATE TABLE sector_beta;
