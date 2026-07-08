-- MANUAL
-- ^ Destructive (bulk DELETE across production tables). deploy.sh skips-and-warns;
--   apply by hand only. Retroactive marker (§7.6): already applied historically — the
--   marker stops boot-time automation from re-running this purge every night.
-- Purge warrant/rights/unit/test-symbol tickers from all production tables.
-- Applied: 2026-06-11
--
-- SOURCE: scripts/audit_universe.py --write flagged 173 tickers has_data=False.
-- These tickers were admitted via three gaps in fetch_exchange_listings.py:
--   1. Nasdaq test symbols (^Z[A-Z]ZZT$): ZJZZT, ZVZZT, ZWZZT, ZXZZT
--   2. Hyphenated warrant/unit suffixes (-WT, -WS, -UN): 66 tickers
--   3. Nasdaq 5th-character convention (len==5, W/R/U suffix, root in universe): 103 tickers
-- fetch_exchange_listings.py patched to reject these classes at ingest time (see commit).
-- All affected tables purged below. sector_beta and relatedness_matrix will be
-- recomputed via dag_stock_relatedness after this migration.

DO $$
DECLARE
    junk_tickers TEXT[] := ARRAY[
        'ACHR-WS', 'ACHR-WT', 'ACONW', 'AERTW', 'AFJKR', 'AFJKU', 'AIMDW', 'AIRJW', 'AISPW', 'AMODW',
        'AMPGR', 'AMPX-WT', 'ASBPW', 'ASPSW', 'BBAI-WT', 'BBBY-WT', 'BCSS-WS', 'BDMDW', 'BEBE-UN', 'BEBE-WS',
        'BEBE-WT', 'BETRW', 'BIII-UN', 'BIII-WS', 'BKSY-WT', 'BNAIW', 'BNZIW', 'BRLSW', 'BSLKW', 'BTSGU',
        'BWIV-UN', 'BZFDW', 'CCIXU', 'CDIOW', 'CELUW', 'CEROW', 'CGCTU', 'COCHW', 'COEPW', 'COPL-WS',
        'CXAIW', 'DBCAU', 'DFLIW', 'DFNSW', 'DSX-WT', 'DTSTW', 'DYNCU', 'DYNCW', 'EDBLW', 'ESLAW',
        'ETHMU', 'EUDAW', 'EVAC-WS', 'EVEX-WT', 'EVLVW', 'FCRS-WS', 'FFAIW', 'FGMCU', 'FLYX-WT', 'FOXXW',
        'FTW-WS', 'GCTS-WT', 'GIBOW', 'GME-WT', 'GPACU', 'GRMLW', 'GRRRW', 'GWH-WT', 'HLLY-WS', 'HLLY-WT',
        'HOLOW', 'HOVRW', 'HUMAW', 'HVIIU', 'INFQ-WS', 'INVZW', 'IONQ-WS', 'IONQ-WT', 'IRAB-WS', 'IRS-WT',
        'JOBY-WS', 'JOBY-WT', 'JSPRW', 'KITTW', 'KPET-UN', 'KRSP-WS', 'LANV-WT', 'LCCCU', 'LCFYW', 'LIDRW',
        'LNZAW', 'LTCHW', 'LUCYW', 'LVWR-WT', 'LZM-WT', 'MAPSW', 'MDAIW', 'MNTSW', 'MOBXW', 'MSAIW',
        'MSPRW', 'MTNE-UN', 'MVSTW', 'MYPSW', 'MZYX-WS', 'NAKAW', 'NCPLW', 'NE-WT', 'NEXRW', 'NIXXW',
        'NOEMR', 'NOEMU', 'NPWR-WT', 'NRXPW', 'NUVB-WS', 'NWAX-UN', 'NWAX-WS', 'NXPLW', 'ONMDW', 'OPENW',
        'OPFI-WS', 'OPFI-WT', 'OPTXW', 'OSRHW', 'OXY-WT', 'PAII-WS', 'PDYNW', 'PERF-WT', 'PEW-WS', 'PIIIW',
        'PL-WT', 'PSNYW', 'PSQH-WT', 'PTIXW', 'RDAGU', 'RDZNW', 'REVBW', 'RMCOW', 'RSVRW', 'SABSW',
        'SAGU-UN', 'SAIHW', 'SBXD-UN', 'SBXD-WT', 'SBXE-UN', 'SBXE-WS', 'SCLXW', 'SDSTW', 'SES-WT', 'SHFSW',
        'SKYH-WT', 'SLDPW', 'SLND-WT', 'SOUNW', 'SPWRW', 'SVCCU', 'TLSIW', 'TRAD-UN', 'TVGNW', 'UWMC-WS',
        'VACI-WS', 'VAL-WT', 'VGASW', 'VLN-WT', 'VSEEW', 'WLDSW', 'YCY-WS', 'YHNAR', 'YHNAU', 'ZJZZT',
        'ZVZZT', 'ZWZZT', 'ZXZZT'
    ];
BEGIN
    DELETE FROM raw_prices            WHERE ticker = ANY(junk_tickers);
    DELETE FROM stock_signals         WHERE ticker = ANY(junk_tickers);
    DELETE FROM signal_features       WHERE ticker = ANY(junk_tickers);
    DELETE FROM llm_analysis          WHERE ticker = ANY(junk_tickers);
    DELETE FROM signal_predictions    WHERE ticker = ANY(junk_tickers);
    DELETE FROM prediction_outcomes   WHERE ticker = ANY(junk_tickers);
    DELETE FROM sector_beta           WHERE ticker = ANY(junk_tickers);
    DELETE FROM relatedness_matrix    WHERE ticker_a = ANY(junk_tickers)
                                         OR ticker_b = ANY(junk_tickers);
    DELETE FROM ticker_metadata       WHERE ticker = ANY(junk_tickers);
END $$;
