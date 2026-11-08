# Runbook

Commands run from the repository root with the venv's Python (`venv-infra\Scripts\python.exe`, written `python` below), on the
desktop or the laptop. `config/local.toml` gives the target root: the quarantine on the desktop, the share that points to it on
the laptop.

## New SPY sessions

1. Start IB Gateway and sign in. API settings: socket port 4001, **Read-Only API** on.
2. Plan, without a connection: `python scripts\ingest\download_ibkr_ohlcv.py --list`
   - By default the plan is every closed session after the last date present in raw and staging.
   - `--start` / `--end` / `--dates` choose other sessions.
3. Download into staging: `python scripts\ingest\download_ibkr_ohlcv.py`
   - The run's manifest path is printed first.
   - Each session prints `complete`, `partial` (saved after the last attempt and flagged), or the error.
4. Check the merge (dry run): `python scripts\ingest\merge_staging_into_raw.py`. Read the decisions: `add`, or
   `skip: <reason>`.
5. Merge: `python scripts\ingest\merge_staging_into_raw.py --apply`
   - `--include-partial` also adds the sessions IBKR has with gaps.
6. Optional, to tidy the folder: `python scripts\ingest\organize_ohlcv_data.py`, then the same command with `--apply`.
7. In `stock_overflow_workspace`, on the desktop, as yourself:
   - `scripts\fix_raw_bad_ticks.py` (dry run first);
   - pipeline step 00;
   - then the refresh of the development view, with `--run-pipeline`. The refresh stops with "pipeline is stale" otherwise.
     It may also slide the holdout window.

## New VIX / VIX3M data

1. Plan: `python scripts\ingest\download_ibkr_index.py --list`
   - Options: `--symbols VIX VIX3M`, `--bars 1min daily`, `--from-start` to fill gaps anywhere.
2. Download: `python scripts\ingest\download_ibkr_index.py`
3. Check the merge, then merge: `python scripts\ingest\merge_index_staging_into_raw.py`, then with `--apply`.
4. Refresh the development view in the research workspace, as above.

## End-to-end check without touching raw (2005 sessions)

```
python scripts\ingest\download_ibkr_ohlcv.py --dates 2005-01-03 2005-01-04 --redownload --staging <root>\store01_rawzone\_ingest\check_redownload\
python scripts\ingest\check_redownload_against_raw.py --check <root>\store01_rawzone\_ingest\check_redownload\
```

The download writes only into the check folder. The comparison only reads, and prints counts per session: minutes in raw, in
the new download, in both, identical, the same numbers written differently, and different. A difference means IBKR's history
changed since the raw file was downloaded, or a bad-tick correction was applied to raw. Delete the check folder afterwards.

## Status and problems

- SPY issues (missing sessions and minutes, duplicates, bars on non-session dates): `python scripts\ingest\find_ohlcv_data_issues.py`
- VIX status by folder: `python scripts\ingest\index_data_status.py`
- **"Another run holds ... .lock".** Another run is writing the same folders, on this machine or the other one. Wait for it.
  If that run is gone (a crash, a closed window), re-run with `--break-lock`.
- **"The target root ... is a development view" / "has no store01_rawzone".** `config/local.toml` points to the wrong folder,
  or the share is not reachable. Nothing was written.
- **"The log ... has other columns".** An old log file sits where the new code wants to write. The code never appends to it;
  leave it as it is.
- Run manifests: `store01_rawzone\_ingest\runs\<run id>.json`, one per writing run. They record whether the run `finished` or
  `failed`, the git commit, the versions, and the counts.

## Tests

`python tests\run_all_tests.py`. This takes about 30 seconds. The tests use a simulated IBKR server and temporary folders only;
they never read `config/local.toml` or any real data.
