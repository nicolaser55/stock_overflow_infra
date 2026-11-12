# Runbook

Commands run from the repository root with the venv's Python (`venv-infra\Scripts\python.exe`, written `python` below), on the
desktop (the laptop ingest was retired on 2026-11-12). `config/local.toml` gives the target root: the quarantine.

Exit codes: 0 done; 1 stopped (nothing written, or the step was undone); 2 finished with problems (some sessions or files were not
saved, or a daily merge was rolled back: see the log; the manifest says `finished_with_problems`). Printed output shows a date only
when it is before `print_dates_before` (`####-##-##` otherwise).

## New SPY sessions

1. Start IB Gateway and sign in. API settings: socket port 4001, **Read-Only API** on.
2. Plan, without a connection: `python scripts\ingest\download_ibkr_ohlcv.py --list`
   - By default the plan is every settled session after the last date present in raw and staging. A session is settled from
     06:00 New York on the calendar day after it: download the morning after (raw keeps the first stored print forever).
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
- **"An interrupted merge left ... journal(s)".** An organize, clean or daily index merge was stopped between moving its source
  files into the backup folder and writing its target (closed window, reboot). Raw may lack those rows, so every script refuses to
  run. Run `python scripts\ingest\restore_pending_merge.py` (dry run: counts), then with `--apply`, then the interrupted script
  again (dry run first). A journal it refuses (files missing or in both places) needs a look by hand: it lists paths only.
- **"The folder ... is outside ...store01_rawzone/".** A `--raw`, `--staging`, `--source` or `--target` folder is not inside the
  quarantine's raw zone (or is inside a development view). Nothing was read or written.
- **"print_dates_before in config/local.toml ...".** Write it as `YYYY-MM-DD`, at or before the start of the holdout window.
- **"The log ... has other columns".** An old log file sits where the new code wants to write. The code never appends to it;
  leave it as it is.
- **"ibapi ... is older than 10.50.1".** The venv was built from an old TWS API. Install a newer one and rebuild the venv with
  `py -3.12 setup_venv.py` (it takes the newest `C:\TWS API*\source\pythonclient`), then `tests\test_installed_ibapi.py`.
- **`IBKR [2188] Up-to-the-second historical data requires additional subscription`** is informational (a code IBKR added in
  October 2026). It concerns bars from the last seconds, not history; the request goes on and the code does not treat it as an error.
- Run manifests: `store01_rawzone\_ingest\runs\<run id>.json`, one per writing run. They record whether the run `finished`,
  `finished_with_problems`, `stopped` (a blocking check: nothing written) or `failed`, the git commit, the versions, and the counts.

## Backup (NAS)

- Every night at 04:10 the task `SO Backup` (SYSTEM) backs up the configured folders; `last_status.json` in the log folder says
  whether it worked (`ok`, `step`, `error`, counts). Task Scheduler's last run result: 0 done, 1 failed, 2 another backup running.
- After any change to `backup\*.ps1`: run `backup\install_backup.ps1 ... -Apply` again (administrator), so the task runs the new copy.
- Each quarter, and after any change: `& 'C:\ProgramData\so_backup\restore_test.ps1' -TestRoot '<quarantine>\_restore_test'`
  (administrator; counts only). Never restore into the development view. Details: `backup/README.md`.

## Tests

`python tests\run_all_tests.py`. This takes about a minute. The tests use a simulated IBKR server and temporary folders only;
they never use the configured target root or any real data (they do read `print_dates_before` and the IBKR settings from
`config/local.toml`).

After any change to the TWS API or the venv: `python tests\test_installed_ibapi.py` (version, callback arguments, account list
dropped, ibapi loggers at WARNING). It is not part of CI, which uses ibapi 9.81 as a stand-in.

## 3-month T-bill rate (FRED DTB3)

One time per machine, store the FRED API key in Windows Credential Manager (input hidden; keep your own copy in Enpass):

```powershell
.\venv-infra\Scripts\python.exe scripts\machine\store_fred_key.py
.\venv-infra\Scripts\python.exe scripts\machine\store_fred_key.py --check
```

Then, by hand, whenever the rate should be brought up to date:

```powershell
.\venv-infra\Scripts\python.exe scripts\ingest\download_fred_series.py            # dry run: fetch, check, compare (nothing written)
.\venv-infra\Scripts\python.exe scripts\ingest\download_fred_series.py --apply    # save the responses, add new rows
```

A run that stops (exit 1) wrote nothing. The summary is counts only.

## SPY dividends (State Street, cross-checked against Yahoo Finance)

1. Download "SPDR Historical Distributions" (XLSX) from ssga.com → Resources → SPDR Dividend Distributions.
2. Dry run, then apply (the script reads Yahoo Finance through yfinance; `--yahoo-file <csv>` reuses an archived cross-check):

```powershell
.\venv-infra\Scripts\python.exe scripts\ingest\import_spy_dividends.py --ssga-file <xlsx>             # dry run
.\venv-infra\Scripts\python.exe scripts\ingest\import_spy_dividends.py --ssga-file <xlsx> --apply     # publish
```

A disagreement with Yahoo, or an ex-date Yahoo does not list, stops the run. Settle it from the fund's annual report and add a
line to `store01_rawzone\_ingest\resolutions\spy_dividends_resolutions.csv` (`ex_date,field,accepted_value,note`), then run again.
