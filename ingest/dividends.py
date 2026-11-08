import os
import json
import shutil
import io
from datetime import datetime, timezone
import pandas as pd
# IMPORT THE CONFIGURATION, THE RUN RECORDS, THE NYSE CALENDAR AND THE RAW FILE LISTING
from ingest import config
from ingest.records import get_file_sha256_str
from ingest.sessions import get_session_pdf, get_ny_now_ts
from ingest.raw_files import get_ohlcv_file_pdf

"""
SPY Dividends (State Street primary, Yahoo Finance cross-check), published append-only with their availability time

Inputs:
    - State Street's "SPDR Historical Distributions" workbook (ssga.com, SPDR Dividend Distributions page, XLSX), downloaded by hand;
      one sheet, header FUND NAME, TICKER, CUSIP, EX-DATE, RECORD DATE, PAYABLE DATE, DIVIDEND ($), ..., every cell text (MM/DD/YYYY,
      amounts with 6 decimals). Only SPY rows with an ex-date from 2005-01-01 up to today (New York) are used.
    - Yahoo Finance's dividend history of SPY (ex-date and amount), fetched with yfinance and kept as a CSV (ex_date, amount_usd);
      or such a CSV given by hand (--yahoo-file). Nasdaq was the approved cross-check but gives no history for non-Nasdaq symbols
      (SPY is listed on NYSE Arca); Nicolas chose Yahoo on 2026-11-08. Yahoo has no record or pay dates: those get internal checks only.
    - Optional resolutions (store01_rawzone/_ingest/resolutions/spy_dividends_resolutions.csv, in the quarantine, never in this
      repository): ex_date, field, accepted_value, note. A resolution settles a cross-source disagreement (from the annual report).

Published rows (store01_rawzone/spy_dividends/spy_dividends.csv, contract version 2):
    date, available_ts, ex_date, record_date, pay_date, declared_date, amount_usd, distribution_type, currency, vintage_kind, ingest_run_id
    - first_seen: available_ts = the ex-date at 09:30 New York (the approved clock); date = ex_date.
    - revision:   State Street later shows other values for a published ex-date; available_ts = the retrieval time (New York).
    - amount_usd is State Street's text (or the accepted value of a resolution); declared_date is empty (no source gives it yet).

Output is counts only; a date is shown only if it is before config.PRINT_DATES_BEFORE_STR; no amount is ever printed.
"""

# DEFINE THE PUBLISHED COLUMNS, THE KEY OF A ROW AND THE KEY OF A DISTRIBUTION
DIVIDEND_COLUMN_STR_LIST = ["date", "available_ts", "ex_date", "record_date", "pay_date", "declared_date", "amount_usd", "distribution_type",
                            "currency", "vintage_kind", "ingest_run_id"]
DIVIDEND_ROW_KEY_STR_LIST = ["ex_date", "distribution_type", "available_ts"]
VALUE_FIELD_STR_LIST = ["record_date", "pay_date", "amount_usd"]
# DEFINE THE FOLDER, FILE, LOCK AND FIRST EX-DATE
DIVIDEND_FOLDER_NAME_STR = "spy_dividends"
DIVIDEND_FILE_NAME_STR = "spy_dividends.csv"
DIVIDEND_LOCK_STR = "spy_dividends"
FIRST_EX_DATE_STR = "2005-01-01"
# DEFINE THE STATE STREET WORKBOOK HEADER AND THE TICKER
SSGA_HEADER_DICT = {"ticker": "TICKER", "ex_date": "EX-DATE", "record_date": "RECORD DATE", "pay_date": "PAYABLE DATE", "amount": "DIVIDEND ($)",
                    "frequency": "FREQUENCY"}
TICKER_STR = "SPY"
# DEFINE THE CROSS-CHECK FILE COLUMNS (YAHOO FINANCE THROUGH yfinance)
CROSSCHECK_COLUMN_STR_LIST = ["ex_date", "amount_usd"]
# DEFINE THE RESOLUTIONS FILE (IN THE QUARANTINE) AND ITS COLUMNS
RESOLUTION_FILE_NAME_STR = "spy_dividends_resolutions.csv"
RESOLUTION_COLUMN_STR_LIST = ["ex_date", "field", "accepted_value", "note"]
# DEFINE THE RESOLUTION FIELDS: A VALUE FIELD (accepted_value REPLACES STATE STREET'S), OR not_in_crosscheck (THE EX-DATE IS ACCEPTED
# THOUGH THE CROSS-CHECK SOURCE DOES NOT LIST IT; accepted_value "accept")
RESOLUTION_FIELD_STR_LIST = ["record_date", "pay_date", "amount_usd", "not_in_crosscheck"]
# DEFINE THE LARGEST PLAUSIBLE AMOUNT AS A SHARE OF THE PREVIOUS CLOSE, AND THE TIME OF THE EX-DATE CLOCK
MAX_YIELD_FLOAT = 0.03
EX_DATE_OPEN_TIME_STR = "09:30:00"

# CLASS: A DIVIDEND INPUT THAT CANNOT BE USED (THE MESSAGE HOLDS NO AMOUNT AND NO RECENT DATE)
class DividendError(RuntimeError):
    pass

# FUNCTION: CONVERT MM/DD/YYYY TEXT TO YYYY-MM-DD ("" STAYS "")
def get_iso_date_str(text_in):
    text_str = "" if text_in is None else str(text_in).strip()
    if text_str in ("", "N/A", "-", "--"):
        return ""
    if hasattr(text_in, "strftime"):
        return text_in.strftime("%Y-%m-%d")
    return datetime.strptime(text_str, "%m/%d/%Y").strftime("%Y-%m-%d")

# FUNCTION: READ THE SPY ROWS OF THE STATE STREET WORKBOOK
def read_ssga_pdf(file_path_str_in):
    """
    Args:
        file_path_str_in (str): The XLSX as downloaded

    Returns:
        pd.DataFrame: ex_date, record_date, pay_date (YYYY-MM-DD), amount_usd (text as in the file), every SPY row
    """
    # OPEN THE FIRST SHEET AND FIND THE HEADER ROW
    import openpyxl
    workbook = openpyxl.load_workbook(file_path_str_in, read_only=True, data_only=True)
    row_list = list(workbook[workbook.sheetnames[0]].iter_rows(values_only=True))
    workbook.close()
    header_index_int = next((index_int for index_int, row in enumerate(row_list) if row and SSGA_HEADER_DICT["ticker"] in [str(cell).strip().upper() for cell in row if cell is not None]), None)
    if header_index_int is None:
        raise DividendError("State Street workbook: no header row with TICKER.")
    header_str_list = [str(cell).strip().upper() if cell is not None else "" for cell in row_list[header_index_int]]
    missing_list = [name_str for name_str in SSGA_HEADER_DICT.values() if name_str not in header_str_list]
    if missing_list:
        raise DividendError(f"State Street workbook: missing columns {missing_list}.")
    column_dict = {key_str: header_str_list.index(name_str) for key_str, name_str in SSGA_HEADER_DICT.items()}
    # KEEP THE SPY ROWS
    spy_list = [row for row in row_list[header_index_int + 1:] if row and row[column_dict["ticker"]] is not None and str(row[column_dict["ticker"]]).strip().upper() == TICKER_STR]
    if not spy_list:
        raise DividendError("State Street workbook: no SPY row.")
    # CONVERT THE DATES; KEEP THE AMOUNT TEXT
    try:
        ssga_pdf = pd.DataFrame({"ex_date": [get_iso_date_str(row[column_dict["ex_date"]]) for row in spy_list],
                                 "record_date": [get_iso_date_str(row[column_dict["record_date"]]) for row in spy_list],
                                 "pay_date": [get_iso_date_str(row[column_dict["pay_date"]]) for row in spy_list],
                                 "amount_usd": [str(row[column_dict["amount"]]).strip() for row in spy_list]})
    except ValueError:
        raise DividendError("State Street workbook: a SPY date is not MM/DD/YYYY.") from None
    return ssga_pdf.sort_values("ex_date").reset_index(drop=True)

# FUNCTION: GET YAHOO FINANCE'S DIVIDEND HISTORY OF SPY AS CSV BYTES (ex_date, amount_usd)
def get_yahoo_bytes():
    """
    Returns:
        bytes: CSV with one row per ex-date (New York date) and the amount as Yahoo gives it
    """
    # ASK yfinance FOR THE FULL HISTORY (THE EX-DATE INDEX IS IN NEW YORK TIME)
    try:
        import yfinance
        dividend_series = yfinance.Ticker(TICKER_STR).dividends
    except Exception as error:
        raise DividendError(f"Yahoo Finance could not be read through yfinance ({type(error).__name__}). Try again later, or pass --yahoo-file.") from None
    if dividend_series is None or len(dividend_series) == 0:
        raise DividendError("Yahoo Finance returned no SPY dividend; the cross-check cannot run.")
    # WRITE THE CSV (FULL PRECISION OF THE FLOAT YAHOO SENT)
    index = dividend_series.index
    date_list = [ts.tz_convert(config.NY_TZ_STR).strftime("%Y-%m-%d") if ts.tzinfo else ts.strftime("%Y-%m-%d") for ts in index]
    crosscheck_pdf = pd.DataFrame({"ex_date": date_list, "amount_usd": [repr(float(value)) for value in dividend_series.values]})
    return crosscheck_pdf.to_csv(index=False, lineterminator="\n").encode("utf-8")

# FUNCTION: PARSE THE CROSS-CHECK CSV
def read_crosscheck_pdf(body_bytes_in):
    """
    Args:
        body_bytes_in (bytes): CSV ex_date (YYYY-MM-DD), amount_usd

    Returns:
        pd.DataFrame: ex_date, amount_usd (text)
    """
    try:
        crosscheck_pdf = pd.read_csv(io.BytesIO(body_bytes_in), dtype=str, keep_default_na=False)
    except (ValueError, pd.errors.ParserError):
        raise DividendError("Cross-check file: not a CSV.") from None
    if list(crosscheck_pdf.columns) != CROSSCHECK_COLUMN_STR_LIST or not crosscheck_pdf["ex_date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise DividendError(f"Cross-check file: columns must be {CROSSCHECK_COLUMN_STR_LIST} with YYYY-MM-DD dates.")
    return crosscheck_pdf.sort_values("ex_date").reset_index(drop=True)

# FUNCTION: READ THE RESOLUTIONS
def read_resolution_pdf(file_path_str_in):
    # NO FILE: NO RESOLUTION
    if not os.path.exists(file_path_str_in):
        return pd.DataFrame(columns=RESOLUTION_COLUMN_STR_LIST)
    resolution_pdf = pd.read_csv(file_path_str_in, dtype=str, keep_default_na=False)
    if list(resolution_pdf.columns) != RESOLUTION_COLUMN_STR_LIST or not resolution_pdf["field"].isin(RESOLUTION_FIELD_STR_LIST).all():
        raise DividendError(f"{file_path_str_in}: columns must be {RESOLUTION_COLUMN_STR_LIST} and field one of {RESOLUTION_FIELD_STR_LIST}.")
    return resolution_pdf

# FUNCTION: GET THE PREVIOUS NYSE SESSION OF EACH DATE, AND WHETHER THE DATE IS A SESSION
def get_session_info_pdf(date_str_list_in):
    session_list = [str(date) for date in get_session_pdf("2004-11-01", max(date_str_list_in))["date"]]
    session_series = pd.Series(session_list)
    info_list = []
    for date_str in date_str_list_in:
        position_int = int(session_series.searchsorted(date_str))
        info_list.append({"date": date_str, "is_session_bool": position_int < len(session_list) and session_list[position_int] == date_str,
                          "previous_session_str": session_list[position_int - 1] if position_int > 0 else ""})
    return pd.DataFrame(info_list, columns=["date", "is_session_bool", "previous_session_str"])

# FUNCTION: GET THE SPY CLOSE OF SOME SESSIONS FROM THE RAW 1-MINUTE FILES
def get_spy_close_dict(date_str_list_in, raw_path_str_in=None):
    """
    Returns:
        dict: {date: last close of the session (float)}; dates with no bar are absent
    """
    # LIST THE RAW FILES AND KEEP THOSE COVERING A WANTED DATE
    file_pdf = get_ohlcv_file_pdf(raw_path_str_in or config.RAW_OHLCV_PATH_STR, alert_in=False)
    wanted_set, close_dict = set(date_str_list_in), {}
    for row in file_pdf.itertuples():
        covered_set = {date_str for date_str in wanted_set if str(row.first_date) <= date_str <= str(row.last_date)}
        if not covered_set:
            continue
        # READ THE FILE; KEEP THE LAST BAR OF EACH WANTED SESSION
        bar_pdf = pd.read_csv(row.file_path_str, usecols=["timestamp", "close", "date"], dtype=str, keep_default_na=False)
        bar_pdf = bar_pdf[bar_pdf["date"].isin(covered_set)]
        if bar_pdf.empty:
            continue
        bar_pdf["ts"] = pd.to_datetime(bar_pdf["timestamp"], utc=True)
        last_pdf = bar_pdf.sort_values("ts").groupby("date").tail(1)
        close_dict.update({date_str: float(close_str) for date_str, close_str in zip(last_pdf["date"], last_pdf["close"])})
    return close_dict

# FUNCTION: BUILD THE ROWS AND THE ISSUES
def get_dividend_tuple(ssga_pdf_in, crosscheck_pdf_in, resolution_pdf_in, published_pdf_in, run_id_str_in, today_str_in, now_ny_ts_in, close_dict_in=None):
    """
    Returns:
        tuple: (rows to add, issue dict {check: offending ex-dates}, count dict)
    """
    # KEEP THE EX-DATES FROM 2005 UP TO TODAY
    ssga_pdf = ssga_pdf_in[(ssga_pdf_in["ex_date"] >= FIRST_EX_DATE_STR) & (ssga_pdf_in["ex_date"] <= today_str_in)].copy().reset_index(drop=True)
    issue_dict, count_dict = {}, {"ssga_spy_row_count_int": int(len(ssga_pdf_in)), "used_row_count_int": int(len(ssga_pdf))}
    # APPLY THE RESOLUTIONS (A RESOLUTION FOR AN EX-DATE THAT IS NOT IN THE FILE IS AN ERROR)
    unknown_list = sorted(set(resolution_pdf_in["ex_date"]) - set(ssga_pdf["ex_date"]))
    issue_dict["blocking_resolution_unknown_ex_date"] = unknown_list
    resolved_set = set()
    for row in resolution_pdf_in.itertuples():
        if row.field in VALUE_FIELD_STR_LIST:
            ssga_pdf.loc[ssga_pdf["ex_date"] == row.ex_date, row.field] = row.accepted_value
        resolved_set.add((row.ex_date, row.field))
    count_dict["resolution_count_int"] = int(len(resolution_pdf_in))
    # CHECKS ON STATE STREET'S ROWS
    session_info_pdf = get_session_info_pdf(sorted(set(ssga_pdf["ex_date"])))
    previous_dict = dict(zip(session_info_pdf["date"], session_info_pdf["previous_session_str"]))
    amount_series = pd.to_numeric(ssga_pdf["amount_usd"], errors="coerce")
    issue_dict["blocking_duplicate_ex_date"] = ssga_pdf.loc[ssga_pdf["ex_date"].duplicated(keep=False), "ex_date"].tolist()
    issue_dict["blocking_ex_date_not_session"] = session_info_pdf.loc[~session_info_pdf["is_session_bool"], "date"].tolist()
    issue_dict["blocking_pay_not_after_ex"] = ssga_pdf.loc[ssga_pdf["pay_date"] <= ssga_pdf["ex_date"], "ex_date"].tolist()
    issue_dict["blocking_record_before_previous_session"] = ssga_pdf.loc[ssga_pdf["record_date"] < ssga_pdf["ex_date"].map(previous_dict), "ex_date"].tolist()
    issue_dict["blocking_amount_not_positive"] = ssga_pdf.loc[~(amount_series > 0), "ex_date"].tolist()
    # FOUR REGULAR DISTRIBUTIONS IN EVERY CLOSED YEAR, AT MOST FOUR IN THE CURRENT ONE (ANOTHER COUNT NEEDS A DECISION ON ITS TYPE)
    year_count_series = ssga_pdf["ex_date"].str[:4].value_counts()
    bad_year_list = [year_str for year_str, count_int in year_count_series.items() if (count_int != 4 and year_str < today_str_in[:4]) or count_int > 4]
    issue_dict["blocking_year_not_four_distributions"] = ssga_pdf.loc[ssga_pdf["ex_date"].str[:4].isin(bad_year_list), "ex_date"].tolist()
    # CROSS-CHECK WITH YAHOO: SAME EX-DATES, AMOUNT EQUAL TO 4 DECIMALS (RESOLVED AMOUNTS EXCEPTED)
    crosscheck_pdf = crosscheck_pdf_in.drop_duplicates("ex_date", keep="first").set_index("ex_date")
    if crosscheck_pdf.empty:
        raise DividendError("Cross-check source has no dividend row; the cross-check cannot run.")
    count_dict["crosscheck_row_count_int"] = int(len(crosscheck_pdf))
    count_dict["matched_row_count_int"] = int(ssga_pdf["ex_date"].isin(crosscheck_pdf.index).sum())
    accepted_missing_set = {date_str for date_str, field_str in resolved_set if field_str == "not_in_crosscheck"}
    issue_dict["blocking_not_in_crosscheck"] = ssga_pdf.loc[~ssga_pdf["ex_date"].isin(crosscheck_pdf.index) & ~ssga_pdf["ex_date"].isin(accepted_missing_set), "ex_date"].tolist()
    window_set = set(ssga_pdf_in.loc[ssga_pdf_in["ex_date"] <= today_str_in, "ex_date"])
    issue_dict["reported_crosscheck_ex_date_not_in_ssga"] = sorted(date_str for date_str in crosscheck_pdf.index if FIRST_EX_DATE_STR <= date_str <= today_str_in and date_str not in window_set)
    other_amount_series = ssga_pdf["ex_date"].map(crosscheck_pdf["amount_usd"])
    disagree_bool_series = other_amount_series.notna() & (pd.to_numeric(other_amount_series, errors="coerce").round(4) != amount_series.round(4))
    resolved_amount_set = {date_str for date_str, field_str in resolved_set if field_str == "amount_usd"}
    issue_dict["blocking_cross_source_disagreement"] = sorted(set(ssga_pdf.loc[disagree_bool_series & ~ssga_pdf["ex_date"].isin(resolved_amount_set), "ex_date"]))
    declared_series = pd.Series("", index=ssga_pdf.index)
    # SANITY: AMOUNT BELOW 3% OF THE PREVIOUS SESSION'S CLOSE (RAW SPY BARS)
    if close_dict_in is not None:
        close_series = ssga_pdf["ex_date"].map(previous_dict).map(close_dict_in)
        issue_dict["reported_no_previous_close"] = ssga_pdf.loc[close_series.isna(), "ex_date"].tolist()
        issue_dict["blocking_amount_over_3pct_of_close"] = ssga_pdf.loc[close_series.notna() & (amount_series / close_series >= MAX_YIELD_FLOAT), "ex_date"].tolist()
    # BUILD THE CANDIDATE ROWS
    ex_ts_series = pd.to_datetime(ssga_pdf["ex_date"] + " " + EX_DATE_OPEN_TIME_STR).dt.tz_localize(config.NY_TZ_STR)
    candidate_pdf = pd.DataFrame({"date": ssga_pdf["ex_date"], "available_ts": ex_ts_series.map(lambda ts: ts.isoformat(sep=" ")),
                                  "ex_date": ssga_pdf["ex_date"], "record_date": ssga_pdf["record_date"], "pay_date": ssga_pdf["pay_date"],
                                  "declared_date": declared_series.values, "amount_usd": ssga_pdf["amount_usd"], "distribution_type": "regular",
                                  "currency": "USD", "vintage_kind": "first_seen", "ingest_run_id": run_id_str_in})[DIVIDEND_COLUMN_STR_LIST]
    # COMPARE WITH THE LATEST PUBLISHED ROW OF EACH DISTRIBUTION
    if published_pdf_in.empty:
        add_pdf = candidate_pdf
        count_dict["revision_count_int"] = 0
    else:
        latest_pdf = published_pdf_in.sort_values("available_ts").groupby(["ex_date", "distribution_type"]).tail(1).set_index(["ex_date", "distribution_type"])
        candidate_key_index = pd.MultiIndex.from_frame(candidate_pdf[["ex_date", "distribution_type"]])
        new_bool_array = ~candidate_key_index.isin(latest_pdf.index)
        known_pdf = candidate_pdf[~new_bool_array].copy()
        latest_known_pdf = latest_pdf.loc[pd.MultiIndex.from_frame(known_pdf[["ex_date", "distribution_type"]])]
        changed_bool_array = (known_pdf[VALUE_FIELD_STR_LIST].values != latest_known_pdf[VALUE_FIELD_STR_LIST].values).any(axis=1)
        revision_pdf = known_pdf[changed_bool_array].copy()
        retrieved_ts = now_ny_ts_in.tz_convert(config.NY_TZ_STR).floor("s")
        revision_pdf["available_ts"], revision_pdf["date"], revision_pdf["vintage_kind"] = retrieved_ts.isoformat(sep=" "), retrieved_ts.strftime("%Y-%m-%d"), "revision"
        add_pdf = pd.concat([candidate_pdf[new_bool_array], revision_pdf], ignore_index=True)
        count_dict["revision_count_int"] = int(len(revision_pdf))
        count_dict["published_unseen_count_int"] = int((~latest_pdf.index.isin(candidate_key_index)).sum())
    count_dict["row_to_add_count_int"] = int(len(add_pdf))
    return add_pdf.reset_index(drop=True), issue_dict, count_dict

# FUNCTION: SUMMARISE WITHOUT AMOUNTS OR RECENT DATES
def get_summary_dict(issue_dict_in, count_dict_in, print_dates_before_str_in):
    summary_dict = dict(count_dict_in)
    for name_str, date_list in issue_dict_in.items():
        summary_dict[name_str] = {"count_int": len(date_list), f"dates_before_{print_dates_before_str_in}": sorted({date_str for date_str in date_list if date_str < print_dates_before_str_in})[:10]}
    return summary_dict

# FUNCTION: READ THE PUBLISHED FILE
def read_published_pdf(file_path_str_in):
    if not os.path.exists(file_path_str_in):
        return pd.DataFrame(columns=DIVIDEND_COLUMN_STR_LIST)
    published_pdf = pd.read_csv(file_path_str_in, dtype=str, keep_default_na=False)
    if list(published_pdf.columns) != DIVIDEND_COLUMN_STR_LIST:
        raise DividendError(f"{file_path_str_in} has other columns than contract version 2; nothing written.")
    return published_pdf

# FUNCTION: RUN (READ, CROSS-CHECK, CHECK, AND PUBLISH WHEN apply)
def run_dividend_dict(ssga_file_path_str_in, apply_bool_in, run_id_str_in, crosscheck_file_path_str_in=None, crosscheck_getter_in=None, today_str_in=None,
                      now_ny_ts_in=None, check_close_bool_in=True):
    """
    Returns:
        dict: Summary (counts; dates only before the print cut-off); "stopped_str" when a blocking check failed (nothing written)
    """
    # PATHS AND CLOCK
    folder_path_str = f"{config.RAWZONE_PATH_STR}{DIVIDEND_FOLDER_NAME_STR}/"
    file_path_str = f"{folder_path_str}{DIVIDEND_FILE_NAME_STR}"
    now_ny_ts = now_ny_ts_in if now_ny_ts_in is not None else get_ny_now_ts()
    today_str = today_str_in or now_ny_ts.strftime("%Y-%m-%d")
    # READ THE INPUTS
    ssga_pdf = read_ssga_pdf(ssga_file_path_str_in)
    if crosscheck_file_path_str_in:
        with open(crosscheck_file_path_str_in, "rb") as file:
            crosscheck_bytes = file.read()
    else:
        crosscheck_bytes = (crosscheck_getter_in or get_yahoo_bytes)()
    crosscheck_pdf = read_crosscheck_pdf(crosscheck_bytes)
    resolution_path_str = f"{config.INGEST_RECORD_PATH_STR}resolutions/{RESOLUTION_FILE_NAME_STR}"
    resolution_pdf = read_resolution_pdf(resolution_path_str)
    published_pdf = read_published_pdf(file_path_str)
    # PREVIOUS CLOSES FOR THE SANITY CHECK
    close_dict = None
    if check_close_bool_in:
        used_list = sorted(ssga_pdf.loc[(ssga_pdf["ex_date"] >= FIRST_EX_DATE_STR) & (ssga_pdf["ex_date"] <= today_str), "ex_date"])
        previous_list = [date_str for date_str in get_session_info_pdf(used_list)["previous_session_str"] if date_str] if used_list else []
        close_dict = get_spy_close_dict(previous_list)
    # BUILD AND CHECK
    add_pdf, issue_dict, count_dict = get_dividend_tuple(ssga_pdf, crosscheck_pdf, resolution_pdf, published_pdf, run_id_str_in, today_str, now_ny_ts, close_dict)
    summary_dict = get_summary_dict(issue_dict, count_dict, config.PRINT_DATES_BEFORE_STR)
    summary_dict["published_row_count_int"] = int(len(published_pdf))
    blocking_list = [name_str for name_str, date_list in issue_dict.items() if name_str.startswith("blocking") and date_list]
    if blocking_list:
        summary_dict["stopped_str"] = f"blocking checks: {blocking_list}"
        return summary_dict
    if not apply_bool_in:
        return summary_dict
    # SAVE THE INPUTS AS RECEIVED
    download_path_str = f"{config.INGEST_RECORD_PATH_STR}downloads/{DIVIDEND_FOLDER_NAME_STR}/{run_id_str_in}/"
    os.makedirs(download_path_str, exist_ok=True)
    shutil.copy2(ssga_file_path_str_in, f"{download_path_str}ssga_historical_distributions.xlsx")
    with open(f"{download_path_str}yahoo_spy_dividends.csv", "wb") as file:
        file.write(crosscheck_bytes)
    if os.path.exists(resolution_path_str):
        shutil.copy2(resolution_path_str, download_path_str)
    summary_dict["download_file_list"] = [{"file_str": name_str, "sha256_str": get_file_sha256_str(f"{download_path_str}{name_str}")} for name_str in sorted(os.listdir(download_path_str))]
    if add_pdf.empty:
        return summary_dict
    # BACK UP, THEN WRITE THE UNION ATOMICALLY (PUBLISHED ROWS UNCHANGED)
    os.makedirs(folder_path_str, exist_ok=True)
    if os.path.exists(file_path_str):
        backup_path_str = f"{config.RAWZONE_PATH_STR}{DIVIDEND_FOLDER_NAME_STR}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}/"
        os.makedirs(backup_path_str, exist_ok=True)
        shutil.copy2(file_path_str, backup_path_str)
    union_pdf = pd.concat([published_pdf, add_pdf], ignore_index=True).sort_values(["ex_date", "available_ts"])[DIVIDEND_COLUMN_STR_LIST]
    if union_pdf.duplicated(DIVIDEND_ROW_KEY_STR_LIST).any():
        raise DividendError("Two rows with the same ex-date, type and availability time; nothing written.")
    temporary_path_str = f"{file_path_str}.tmp"
    union_pdf.to_csv(temporary_path_str, index=False, lineterminator="\n")
    os.replace(temporary_path_str, file_path_str)
    summary_dict["published_sha256_str"] = get_file_sha256_str(file_path_str)
    return summary_dict
