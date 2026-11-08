# What changed from the old `stock_overflow_data_ingest` (D1, 2026-11-08)

This repository starts with a fresh history. The old repository was public with notebook outputs in its history; it was deleted
on 2026-11-08, and Nicolas keeps a zip archive of it. The audit behind this list is `claude/DATA_INGEST_REVIEW_2026-11-08.md`
in the project.

## Ported, behaviour unchanged

These keep the same downloads, the same file format, the same merge rules and the same pacing:
- the IBKR client;
- the SPY 1-minute pipeline (session selection, download, checks, staging);
- the text-preserving merge, organize, clean, rebuild and issue report;
- the VIX / VIX3M pipeline.

Two pieces from the research workspace are now reproduced here instead of imported:
- **The NYSE schedule.** It is the same function on top of the same `pandas_market_calendars` version. Checked on 2026-11-08:
  identical to the workspace's result for all 5,534 sessions from 2005 to 2026.
- **The candlestick sanity check.** Same checks; identical result on a 5,000-row synthetic frame with injected faults.

## Fixed

| Was | Now |
|---|---|
| `import so` everywhere. The import failed unless `SO_DATA_PATH` was set, and the tests called a research function that no longer exists. | No import of the research package (checked by the repository guard). The tests read raw folders with a copy of the research reader (`tests/contract_reader.py`). |
| The data root defaulted to a network share address written in the code. | The target root comes from `SO_INGEST_TARGET_ROOT` or `config/local.toml` (git-ignored). With neither, every script stops. |
| No check that the root was the right tree. | Scripts refuse an unset root, a root without `store01_rawzone/`, a development view (`VIEW.json`, `view_manifest.json`), and `SO_DATA_PATH`. |
| The merge log was written once at the end: a run that failed halfway left added days with no record. | Each added file is logged as soon as it is added. A copy that fails verification is removed from raw, and its staging file is kept. |
| A log was appended to without checking its header, so a column change misaligned old and new rows. | A log with other columns is refused. New logs are `download_log_v2.csv` and `merge_log_v2.csv`, which add the run id and the file SHA-256; the old logs stay as they are. |
| No run record and no lock. | One manifest per writing run (`_ingest/runs/`), and one lock per source family (`spy_1min`, `vix_family`), released even when the run fails; `--break-lock` for a dead run. |
| ibapi would log the account list at INFO if logging were ever turned on. | The callback drops the account list; the ibapi loggers are pinned to WARNING; a test checks both. |
| A hard-coded "untouched window" date (2026-05-14), stale since the holdout became a sliding window. | Removed: the ingest knows nothing about the holdout. |
| Bad ticks were counted with the research's rule imported into the ingest. | Not judged here. The research's `fix_raw_bad_ticks.py` is the only rule (decided 2026-11-08). |
| No CI. | GitHub Actions: the suites on Linux and Windows (ibapi 9.81.1.post1 from PyPI), and a gitleaks scan of the history. |

## Removed (decided 2026-11-08)

- Google Cloud Storage (`gcs_file_management.py`, `gcs_mount.py`). The service account and the bucket were deleted the same day.
- Every notebook: `step01` and `step03` duplicated the scripts, `step02` was the live stream, and `legacy/` was yfinance and
  TimescaleDB.
- The live stream (`ibkr_stream.py`): the research does not use it.
- The AAPL pipeline (`stock_*.py`, its scripts and tests). Its folders on the data tree are left as they are; `index_data_status.py`
  now lists them as "other folders".
- The one-off VIX probe and alignment tools, and their tests. They are in the old archive.
- `setup_venv.py`'s installation of the research workspace and of a Jupyter kernel.

## Known limits, not fixed in D1

- **The download loop still exists twice** (SPY in `ibkr_download.py`, index in `index_download.py`). Merging the two risks
  changing download behaviour, so it waits for a delivery with time to test it against a real Gateway.
- **A partial session keeps the bars of its last attempt**, not its best.
- **A dropped Gateway connection is not detected at once.** Pending requests are not released on the connectivity codes
  1100 to 1102; they wait for their 60 s timeout.
- **CI tests with ibapi 9.81.1.post1**, the version on PyPI; the machines use 10.45.1. The client supports both, and the
  simulated server sends both error layouts, but CI never runs the real 10.45.1 package.
- **Nothing here has run against a live IB Gateway.** The first real run is part of D1's "done".
