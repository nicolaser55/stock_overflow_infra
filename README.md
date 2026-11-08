# stock_overflow_infra

Machine-side code of the Stock Overflow project:
- **`ingest/`** downloads market data from IBKR (SPY 1-minute bars; VIX and VIX3M daily and 1-minute bars), checks it and adds
  it to the project's complete data tree, the quarantine. It also brings in the 3-month T-bill rate (FRED DTB3) with every
  first release and revision, and (next) SPY dividends.
- **`backup/`** (to come) will hold the NAS backup and archive jobs.

The research itself lives in [`stock_overflow_workspace`](https://github.com/nicolaser55/stock_overflow_workspace). That
workspace reads the files this repository writes and never imports its code. The interface is
[`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md).

**This repository is public.** It holds no data, no logs, no notebook outputs, no machine addresses or paths, and no
credentials. `tests/test_repo_guard.py` and CI check this on every push. See [`SECURITY.md`](SECURITY.md).

## Rules the code enforces

- **It writes only to the quarantine**, never to the development view. Every script checks the target root first.
- **Downloads go to staging.** A separate merge adds checked files to the raw folders. The merge is a dry run unless `--apply`
  is given, and it never replaces a date that is already in raw.
- **Every writing run leaves a record**: a manifest in `store01_rawzone/_ingest/runs/`, plus the run id and a SHA-256 in every
  log row. It also holds a lock, so the two machines cannot write the same folders at the same time.
- **No account data is ever requested.** The IBKR client drops the account list the Gateway sends at connection, and it keeps
  ibapi's logging at WARNING. Turn on **Read-Only API** in the Gateway.
- **It knows nothing about the holdout window.** Which dates the research may see is decided only by its development view,
  which Nicolas refreshes.

## Setup (once per machine)

Requirements:
- Python 3.12, through the Windows `py` launcher;
- git;
- the TWS API installed, so that `C:\TWS API\source\pythonclient` exists (ibapi 10.45.1 is not on PyPI).

```powershell
git clone https://github.com/nicolaser55/stock_overflow_infra.git <folder>
cd <folder>
py -3.12 setup_venv.py                      # creates venv-infra; ibapi from the TWS API source
Copy-Item config\local.example.toml config\local.toml
notepad config\local.toml                   # target_root = the quarantine (desktop) or the share that points to it (laptop)
venv-infra\Scripts\python.exe tests\run_all_tests.py
```

## Running

Every command below runs from the repository root with `venv-infra\Scripts\python.exe`. [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
has the full sequence, with the steps that follow in the research workspace.

| Step | SPY 1-minute | VIX / VIX3M |
|---|---|---|
| See what would be downloaded (no connection) | `scripts\ingest\download_ibkr_ohlcv.py --list` | `scripts\ingest\download_ibkr_index.py --list` |
| Download into staging (IB Gateway signed in) | `scripts\ingest\download_ibkr_ohlcv.py` | `scripts\ingest\download_ibkr_index.py` |
| Check the merge (dry run) | `scripts\ingest\merge_staging_into_raw.py` | `scripts\ingest\merge_index_staging_into_raw.py` |
| Merge | add `--apply` | add `--apply` |
| Tidy the raw folder (dry run, then `--apply`) | `scripts\ingest\organize_ohlcv_data.py`, `clean_ohlcv_data.py` | – |
| Status and issues (read only) | `scripts\ingest\find_ohlcv_data_issues.py` | `scripts\ingest\index_data_status.py` |
| Compare a re-download with raw (read only) | `scripts\ingest\check_redownload_against_raw.py` | – |

**3-month T-bill rate (FRED DTB3):** store the API key once with `scripts\machine\store_fred_key.py`, then
`scripts\ingest\download_fred_series.py` (dry run) and `--apply`. No IB Gateway needed.

Only one IBKR session per username can be signed in at a time. Signing in on one machine signs the other out.

## Layout

```
ingest/              the package (see ingest/__init__.py)
scripts/ingest/      command-line tools (dry run by default for every tool that changes raw files)
scripts/machine/     machine helpers run by hand: access rights (administrator PowerShell), FRED key into Credential Manager
tests/               plain-assert suites, simulated IBKR server, temporary folders only
docs/                data contract, runbook, what changed from the old ingest repository
config/              local.example.toml (template); local.toml is machine-specific and git-ignored
```
