-- MANUAL
-- ^ Destructive (DELETE). deploy.sh skips-and-warns; apply by hand via the SSM
--   tunnel. Idempotent (re-run deletes nothing once clean), but never auto-applied.
--
-- Inauguration §8 Step 1b cleanup (defect #10). backfill_eodhd wrote EODHD's
-- zero-filled NO-TRADE rows verbatim: 2,023 rows with close <= 0, of which 1,947
-- are all-OHLC-zero / zero-volume (a $47 stock like HNGE with an O=H=L=C=0 row on a
-- non-trading day). These are NOT bankruptcy $0 — 36/50 affected tickers are non-Q,
-- 48/50 zeros are mid-series (real prices resume after), and 35/50 have median
-- close >= $1 so V2_MIN_PRICE does NOT fence them: they would poison returns on
-- real-priced names. Row-level verification failed the "post-bankruptcy zeros"
-- story; ruled STOP.
--
-- This deletes the corrupt rows so Step 1b re-passes. The ROOT CAUSE — ingest
-- accepting zero/no-trade bars — is fixed separately by the ingest zero-bar guard
-- (blocks NIGHTLY-ENABLE, joins D11's gate); without that guard the nightly run
-- re-introduces these rows, so this migration is a one-time inauguration cleanup,
-- not the fix.
--
-- Applied: MANUAL — pending (run once over the tunnel this inauguration session)
-- Expected: 2,023 rows deleted. Re-run after fix: 0.

DELETE FROM raw_prices
    WHERE close <= 0;
