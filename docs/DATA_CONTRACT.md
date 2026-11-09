# Data contract: what this repository writes for `stock_overflow_workspace`

**Contract version 2 (2026-11-08): version 1 plus the 3-month T-bill rate (DTB3) and SPY dividends. No research loader reads
version 2 yet (delivery D3).** The research workspace reads files only and never imports this repository. This document
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
├── fred_dtb3/dtb3.csv                    3-month T-bill rate, every first release and revision (version 2)
├── spy_dividends/spy_dividends.csv      SPY distributions from 2005, first release and revisions (version 2)
└── _ingest/                             ingest records: runs/<run id>.json (manifests), locks/<name>.lock,
                                         downloads/<source>/<run id>/ (responses as downloaded),
                                         resolutions/ (Nicolas's decisions on source disagreements)
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

## Non-bar sources: common rules (version 2)

- **`date` is the New York date of `available_ts`**, never the date the value describes (that has its own column). The
  development-view refresh cuts undated files on `date`, so a row is in the view only once it was public.
- **`available_ts`** is text, New York time with its offset, e.g. `2005-01-04 16:30:00-05:00`.
- **Append-only.** A published row is never edited or removed. A correction is a new row with its own `available_ts`.
  A source that changes an already-published vintage stops the run; nothing is written.
- **Responses are kept as downloaded** under `_ingest/downloads/<source>/<run id>/`, with their SHA-256 in the run manifest.
- **Output is counts only.** A printed line or a manifest names a date only if it is before `print_dates_before` (machine
  setting, at or before the start of the holdout window), and never a value.

## `fred_dtb3/dtb3.csv`: 3-month Treasury bill, secondary market, discount basis (FRED DTB3, H.15)

| Column | Meaning |
|---|---|
| `date` | New York date of `available_ts` |
| `available_ts` | When the value was public (below) |
| `observation_date` | The business day the rate describes |
| `value_pct` | Percent, discount basis, **text as FRED sent it**; `.` = no value that day |
| `vintage_realtime_start` | ALFRED real-time start of this value (the day FRED first showed it) |
| `vintage_kind` | `first_seen`, `rule_based_backfill` or `revision` |
| `ingest_run_id` | Run that first published the row |

- **Key:** `(observation_date, vintage_realtime_start)`. One row per observation is not `revision`; it is its first release.
- **`first_seen`:** `available_ts` = the **later** of `vintage_realtime_start` and the next US federal business day after
  `observation_date`, at 16:30 New York. The floor (decided 2026-11-08) covers ALFRED records that put a posting on or before
  the observation date (seen for two dates in 2015) or on a weekend or holiday; `vintage_realtime_start` keeps ALFRED's date.
- **`rule_based_backfill`:** an observation older than the series' earliest ALFRED vintage has no record of its first posting.
  `available_ts` = the next US federal business day after `observation_date`, at 16:30 New York. This is an assumption, not a
  record.
- **`revision`:** a later real-time period whose value differs from the one before; `available_ts` = its
  `vintage_realtime_start` at 16:30 New York, with the same floor. Periods that repeat the same value are not rows.
- **Holiday calendar:** pandas' `USFederalHolidayCalendar` applies today's holiday rules to every year, so before 1971 a
  backfilled clock can be off by about a day. It does not affect any date from 2005 on.
- **Not done here (research, R4b):** the conversion from discount basis to an investment yield, the accrual over calendar days,
  and carrying the last value over sessions with no value.
- **Checks before a publish** (any failure stops it): one row per key; one first release per observation; no observation or
  vintage after the retrieval date; `available_ts` after the observation date; values parse and lie in [−1, 20].
  **Reported:** rows moved later by the floor, observations on a weekend or federal holiday, weekdays with no observation (holiday or not), first releases
  of `.`, and observations whose first release differs from the latest.

## `spy_dividends/spy_dividends.csv`: SPY distributions (State Street, cross-checked against Yahoo Finance)

| Column | Meaning |
|---|---|
| `date` | New York date of `available_ts` |
| `available_ts` | When the row was public (below) |
| `ex_date` | Ex-dividend date (`YYYY-MM-DD`, an NYSE session) |
| `record_date`, `pay_date` | Record and payable dates, as State Street gives them |
| `declared_date` | Declaration date; empty (no source gives it yet) |
| `amount_usd` | Dollars per share, **text as State Street gives it** (6 decimals), or the accepted value of a resolution |
| `distribution_type` | `regular` (the only type so far; any other needs a decision first) |
| `currency` | `USD` |
| `vintage_kind` | `first_seen` or `revision` |
| `ingest_run_id` | Run that first published the row |

- **Source:** State Street's "SPDR Historical Distributions" workbook (ssga.com, SPDR Dividend Distributions page), downloaded
  by hand and kept as received under `_ingest/downloads/spy_dividends/<run id>/`. Ex-dates from 2005-01-01 up to the run date.
- **`first_seen`:** `available_ts` = the ex-date at **09:30 New York** (the approved clock); `date` = `ex_date`. This assumes
  the amount was public by the open of the ex-date. State Street announces before the ex-date, but that cannot be proven for
  every past row.
- **`revision`:** if State Street later shows another record date, pay date or amount for a published ex-date, a new row is
  added with `available_ts` = the retrieval time (New York). The earlier row stays. A consumer takes, for each
  `(ex_date, distribution_type)`, the latest row with `available_ts` at or before its decision time.
- **Cross-check (blocking), decided 2026-11-08:** Yahoo Finance (through `yfinance`), archived as
  `yahoo_spy_dividends.csv` with each run. Every ex-date must be listed by Yahoo, with an amount within 0.0005 of State Street's (Yahoo rounds
  to 3 decimals; decided 2026-11-09), so Yahoo confirms each amount to about ±0.0005 USD, not to State Street's 6 decimals. Nasdaq, the
  first choice, gives no history for symbols not listed on Nasdaq (SPY is on NYSE Arca). Yahoo has no record or pay dates; those
  get the internal checks only. A disagreement or a missing ex-date stops the run until Nicolas settles it from the annual report,
  in `_ingest/resolutions/spy_dividends_resolutions.csv` (columns `ex_date, field, accepted_value, note`; `field` is
  `record_date`, `pay_date`, `amount_usd`, or `not_in_crosscheck` with `accept`; quarantine only).
- **Other blocking checks:** the ex-date is an NYSE session; one row per ex-date; the pay date is after the ex-date; the record
  date is not before the previous session; the amount is positive and below 3% of SPY's previous close (raw 1-minute bars);
  exactly 4 distributions in each closed year from 2005, at most 4 in the current year.
- **Reported:** Yahoo ex-dates from 2005 that State Street does not list; sessions with no raw close.
- **Accounting (research, R4b):** credited on the ex-date to the shares held at the previous close.

