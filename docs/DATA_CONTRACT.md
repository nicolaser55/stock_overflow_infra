# Data contract: what this repository writes for `stock_overflow_workspace`

**Contract version 1 (2026-11-08).** The research workspace reads files only and never imports this repository. This document
is the whole interface between the two. A change to any folder name, file name, column or meaning below raises the version.
That change lands in this file, in `tests/contract_reader.py`, and in the research side (`so/paths.py`, the loaders,
`docs/DATA_SOURCES.md`) at the same time. The research files are protected, so Nicolas approves each change.

## Where it is written

Everything goes under the **target root**: the complete data tree, called the quarantine (`ingest.config`). The target root is
**never** the development view (`SO_DATA_PATH`). Only Nicolas builds the view from the quarantine, with the research script
`scripts/refresh_development_view.py`. The ingest knows nothing about the holdout window.

Every script refuses a target root that:
- is not set;
- has no `store01_rawzone/`;
- holds `VIEW.json` or `view_manifest.json`;
- equals `SO_DATA_PATH`.

```
<target root>/store01_rawzone/
├── ibkr_spy_1min/                       SPY 1-minute bars (research: so.paths.LOCAL_OHLCV_DATA_FILE_PATH_STR)
├── ibkr_vix_family/{vix,vix3m}_{1min,daily}/   VIX and VIX3M bars (research: LOCAL_VIX_*_DATA_FILE_PATH_STR)
├── ibkr_spy_1min_staging/               SPY downloads not yet merged, merged/, download_log_v2.csv, merge_log_v2.csv
├── ibkr_vix_family_staging/             the same for VIX / VIX3M
├── <raw folder>_backup_YYYYMMDD_HHMMSS/ copies made before any raw file is changed
└── _ingest/                             ingest records: runs/<run id>.json (manifests), locks/<name>.lock
```

The research reads only the raw folders. The staging, backup and `_ingest` folders are not named in `so.paths`, so the
refresh never copies them into the development view.

## Bar files (SPY, VIX, VIX3M)

| Item | Rule |
|---|---|
| Files | `ohlcv_data_YYYY.csv` (a past year), `ohlcv_data_YYYYMM.csv` (a past month of the current year), `ohlcv_data_YYYYMMDD.csv` (a day). The research reads every `*.csv` directly in the folder, whatever the layout, and no sub-folder. **Nothing else with a `.csv` name is ever written in a raw folder.** |
| Columns | `timestamp, open, high, low, close, volume, created_ts, date` |
| `timestamp` | New York time with its offset, as text, e.g. `2026-07-17 09:30:00-04:00`. 1-minute bars are stamped at the **start** of the minute. Daily index bars are stamped midnight New York: a date label, not a time. |
| Prices | Text as IBKR sent it; never re-formatted. |
| `volume` | SPY: shares. Index: empty when IBKR sends its "unset" value (2**127 − 1). |
| `created_ts` | Always empty (kept for compatibility with every existing file). |
| `date` | `YYYY-MM-DD`, the New York session date. |
| Sessions | SPY: NYSE regular hours, every minute from 09:30 to the last bar (15:59, or 12:59 on half days). A session with missing minutes is added only with `--include-partial`, and its gaps are in the download log. |
| Revisions | **First stored print, add-only.** A date already in raw is never replaced. Daily index files merge with **existing rows winning**: a later IBKR correction is counted as a conflict in the merge log, never written. |
| Bad ticks | Not judged here. The research's `scripts/fix_raw_bad_ticks.py` is the only rule; run it after a merge. Before a merge, this repository checks candlestick consistency only: high and low against the open and close, prices above zero, volume not negative, no missing value, no minute twice. |

**Availability clocks** are applied by the research's point-in-time registry, not written into bar files:
- 1-minute bars: the label + 1 minute.
- Daily index bars: the bar's date at 16:15 New York.

## Provenance

- Every download attempt is a row of the staging folder's `download_log_v2.csv`. Each row holds the ibapi version, the request,
  the outcome, the run id and the SHA-256 of the file it saved.
- Every file added to raw is a row of `merge_log_v2.csv`. Each row holds the SHA-256 of the raw file after the step and the run id.
- Every writing run has a manifest `_ingest/runs/<run id>.json`, holding:
  - the script and its arguments (secrets masked);
  - the start and end time in UTC;
  - the git commit of this repository, and whether the clone had uncommitted changes;
  - the Python and package versions;
  - the target root, the status (`running`, `finished`, `failed` with the error), and counts. Never data values.
- The old logs (`download_log.csv`, `merge_log.csv`, written by the old ingest repository) are kept as they are. The new code
  refuses to append to a log whose header differs from its own.

## Sources to come (contract version 2, delivery D2)

- `spy_dividends/spy_dividends.csv` (SPY dividends);
- `fred_dtb3/dtb3.csv` (3-month T-bill rate).

Their columns, clocks and revision rules are fixed in `claude/DATA_INGEST_REVIEW_2026-11-08.md` §7.9 and §11 of the project. They
will be added here with their code.
