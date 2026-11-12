# stock_overflow_infra

Machine-side code of the Stock Overflow project:
- **`ingest/`** downloads market data from IBKR (SPY 1-minute bars; VIX and VIX3M daily and 1-minute bars), checks it and adds
  it to the project's complete data tree, the quarantine. It also brings in the 3-month T-bill rate (FRED DTB3) with every
  first release and revision, and SPY dividends (State Street, cross-checked against Yahoo Finance).
- **`backup/`** (to come) will hold the NAS backup and archive jobs.

The research itself lives in [`stock_overflow_workspace`](https://github.com/nicolaser55/stock_overflow_workspace). That
workspace reads the files this repository writes and never imports its code. The interface is
[`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md).

**This repository is public.** It holds no data, no logs, no notebook outputs, no machine addresses or paths, and no
credentials. `tests/test_repo_guard.py` and CI check this on every push. See [`SECURITY.md`](SECURITY.md).

## Rules the code enforces

- **It writes only to the quarantine**, never to the development view. Every script checks the target root first, and every folder
  given on the command line (`--raw`, `--staging`, `--source`, `--target`) must lie inside the quarantine's `store01_rawzone/`.
- **An interrupted roll-up is never silent.** A merge step keeps a journal until its target is verified. If the process dies in
  between, every script refuses to run until `scripts\ingest\restore_pending_merge.py` has put the files back.
- **Downloads go to staging.** A separate merge adds checked files to the raw folders. The merge is a dry run unless `--apply`
  is given, and it never replaces a date that is already in raw.
- **Every writing run leaves a record**: a manifest in `store01_rawzone/_ingest/runs/`, plus the run id and a SHA-256 in every
  log row. It also holds a lock, so the two machines cannot write the same folders at the same time.
- **No account data is ever requested.** The IBKR client drops the account list the Gateway sends at connection, masks any
  account id in IBKR's messages, and keeps ibapi's logging at WARNING. Turn on **Read-Only API** in the Gateway.
- **ibapi 10.50.1 or later.** The client refuses to connect with an older ibapi (IBKR's minimum from 2026-12-15).
- **Output names no recent date.** Everything the tools print (bar tools included) shows a date only when it is before
  `print_dates_before`; later dates print as `####-##-##` and account ids as `U*******`. Logs and manifests in the quarantine keep
  the full detail.
- **A session is taken only once settled**: from 06:00 New York on the calendar day after it (raw keeps the first stored print).
- **It knows nothing about the holdout window.** Which dates the research may see is decided only by its development view,
  which Nicolas refreshes.

## Setup (once per machine)

Requirements:
- Python 3.12, through the Windows `py` launcher;
- git;
- the TWS API 10.50.1 or later installed (each version in its own `C:\TWS API <version>` folder; the official ibapi 10.x is not
  on PyPI). `setup_venv.py` takes the newest `C:\TWS API*\source\pythonclient`, or `--ibapi-source <folder>`.

Ingest runs on the desktop only (the laptop ingest was retired on 2026-11-12).

```powershell
git clone https://github.com/nicolaser55/stock_overflow_infra.git <folder>
cd <folder>
py -3.12 setup_venv.py                      # creates venv-infra; ibapi from the newest TWS API source
Copy-Item config\local.example.toml config\local.toml
notepad config\local.toml                   # target_root = the quarantine
venv-infra\Scripts\python.exe tests\run_all_tests.py          # logic tests (simulated IBKR server)
venv-infra\Scripts\python.exe tests\test_installed_ibapi.py   # the installed ibapi (machine only, not in CI)
.\scripts\machine\install_git_hooks.ps1                       # pre-push guard: no push if the repository guard fails
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
| Put back an interrupted roll-up (dry run, then `--apply`) | `scripts\ingest\restore_pending_merge.py` | the same script |

**3-month T-bill rate (FRED DTB3):** store the API key once with `scripts\machine\store_fred_key.py`, then
`scripts\ingest\download_fred_series.py` (dry run) and `--apply`. No IB Gateway needed.

**SPY dividends:** `scripts\ingest\import_spy_dividends.py --ssga-file <State Street XLSX>` (dry run) and `--apply`
([`docs/RUNBOOK.md`](docs/RUNBOOK.md)).

Only one IBKR session per username can be signed in at a time.

**CI** runs the logic tests on Linux and Windows with ibapi 9.81.1.post1 from PyPI as a stand-in (the simulated server sends every
ibapi layout), the repository guard, and gitleaks. It never runs the real ibapi: that is checked on the machine
(`tests\test_installed_ibapi.py`) and enforced at connection.

## Layout

```
ingest/              the package (see ingest/__init__.py)
scripts/ingest/      command-line tools (dry run by default for every tool that changes raw files)
scripts/machine/     machine helpers run by hand: access rights (administrator PowerShell), FRED key into Credential Manager, git hooks
tests/               plain-assert suites, simulated IBKR server, temporary folders only
docs/                data contract, runbook, what changed from the old ingest repository
config/              local.example.toml (template); local.toml is machine-specific and git-ignored
```
