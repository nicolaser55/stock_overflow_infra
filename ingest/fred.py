import os
import json
import shutil
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
# IMPORT THE CONFIGURATION AND THE RUN RECORDS
from ingest import config
from ingest.records import get_file_sha256_str

"""
FRED / ALFRED Series With First-Release Vintages (DTB3 first; UNRATE later reuses this module)

One request series per run: every real-time period of every observation (FRED API series/vintagedates, then series/observations
with output_type 1 in windows of at most 1000 vintage dates), paged. The pages are saved as downloaded, with their SHA-256, before parsing.

Published rows (contract version 2, docs/DATA_CONTRACT.md):
    date, available_ts, observation_date, value_pct, vintage_realtime_start, vintage_kind, ingest_run_id
    - first_seen:           the first real-time period of an observation; available_ts = its realtime_start at 16:30 New York.
    - rule_based_backfill:  the first period of an observation older than the series' earliest vintage (ALFRED has no record of its
                            first posting); available_ts = the next US federal business day after the observation at 16:30 New York.
    - revision:             a later period whose value differs from the previous one; available_ts = its realtime_start at 16:30.
    - date = the New York date of available_ts (the development-view refresh cuts on it).
    - value_pct is the text FRED sent ("." = no value); never converted here.

Publishing is append-only: a published row is never edited or removed. A fetched row whose key (observation_date,
vintage_realtime_start) is already published with a different value or kind stops the run.

Nothing printed names an observation date on or after the print cut-off (config.PRINT_DATES_BEFORE_STR), and no value is printed.
The API key comes from Windows Credential Manager (keyring) and never appears in a printed line, a file or a manifest.
"""

# DEFINE THE FRED API
FRED_API_URL_STR = "https://api.stlouisfed.org/fred/"
# DEFINE THE MOST VINTAGE DATES IN ONE OBSERVATION REQUEST (API LIMIT 2000 FOR JSON)
VINTAGE_WINDOW_INT = 1000
# DEFINE THE KEYRING ENTRY OF THE API KEY (scripts/machine/store_fred_key.py WRITES IT)
KEYRING_SERVICE_STR = "stock_overflow_infra"
KEYRING_USERNAME_STR = "fred_api_key"
# DEFINE THE PAGE SIZE (THE API MAXIMUM)
PAGE_LIMIT_INT = 100000
# DEFINE THE HOUR AT WHICH A FRED POSTING DAY COUNTS AS PUBLIC (FRED POSTS H.15 DAILY RATES AT ABOUT 15:15-16:15 NEW YORK)
AVAILABLE_TIME_STR = "16:30:00"
# DEFINE THE PUBLISHED COLUMNS AND THE KEY
FRED_COLUMN_STR_LIST = ["date", "available_ts", "observation_date", "value_pct", "vintage_realtime_start", "vintage_kind", "ingest_run_id"]
FRED_KEY_STR_LIST = ["observation_date", "vintage_realtime_start"]

# DEFINE THE SERIES THIS MODULE PUBLISHES
FRED_SERIES_SPEC_DICT = {
    "DTB3": {"folder_str": "fred_dtb3", "file_str": "dtb3.csv", "lock_str": "fred_dtb3", "value_min_float": -1.0, "value_max_float": 20.0,
             "business_day_bool": True},
}

# CLASS: A FRED REQUEST THAT FAILED (THE MESSAGE NEVER HOLDS THE URL OR THE KEY)
class FredError(RuntimeError):
    pass

# FUNCTION: READ THE API KEY
def get_fred_api_key_str():
    """
    Returns:
        str: The FRED API key stored in Windows Credential Manager (raises FredError if there is none)
    """
    # READ THE KEY FROM THE OPERATING SYSTEM'S CREDENTIAL STORE
    import keyring
    try:
        key_str = keyring.get_password(KEYRING_SERVICE_STR, KEYRING_USERNAME_STR)
    except Exception as error:
        raise FredError(f"No FRED API key: the credential store cannot be read ({type(error).__name__}).") from None
    if not key_str:
        raise FredError("No FRED API key in the credential store. Run: python scripts/machine/store_fred_key.py")
    # RETURN THE KEY
    return key_str.strip()

# FUNCTION: SEND ONE REQUEST TO THE FRED API
def get_fred_response_bytes(endpoint_str_in, query_dict_in, api_key_str_in, timeout_int_in=60):
    """
    Args:
        endpoint_str_in (str): "series/observations" or "series/vintagedates"
        query_dict_in (dict): Query parameters without the key and file type
        api_key_str_in (str): API key
        timeout_int_in (int): Seconds

    Returns:
        bytes: The response body as downloaded (JSON)
    """
    # BUILD THE REQUEST
    query_dict = {**query_dict_in, "api_key": api_key_str_in, "file_type": "json"}
    request = urllib.request.Request(f"{FRED_API_URL_STR}{endpoint_str_in}?{urllib.parse.urlencode(query_dict)}", headers={"User-Agent": "stock_overflow_infra"})
    # SEND IT; ERRORS ARE RE-RAISED WITHOUT THE URL (IT HOLDS THE KEY)
    try:
        with urllib.request.urlopen(request, timeout=timeout_int_in) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        try:
            message_str = json.loads(error.read().decode("utf-8")).get("error_message", "")
        except Exception:
            message_str = ""
        raise FredError(f"FRED answered HTTP {error.code}. {message_str}".replace(api_key_str_in, "***")) from None
    except urllib.error.URLError as error:
        raise FredError(f"FRED could not be reached ({type(error.reason).__name__}).") from None

# FUNCTION: GET EVERY PAGE OF ONE QUERY
def get_paged_body_list(getter_in, endpoint_str_in, query_dict_in, list_key_str_in):
    """
    Args:
        getter_in (callable): (endpoint, query dict) -> bytes
        endpoint_str_in (str): API endpoint
        query_dict_in (dict): Query parameters
        list_key_str_in (str): Name of the list in the response ("observations" or "vintage_dates")

    Returns:
        list[bytes]: Response bodies in order
    """
    # PAGE THROUGH THE ROWS
    body_list, offset_int = [], 0
    while True:
        body_bytes = getter_in(endpoint_str_in, {**query_dict_in, "limit": PAGE_LIMIT_INT if list_key_str_in == "observations" else 10000, "offset": offset_int})
        body_dict = json.loads(body_bytes.decode("utf-8"))
        if list_key_str_in not in body_dict:
            raise FredError(f"FRED {endpoint_str_in} answer at offset {offset_int} has no {list_key_str_in} ({body_dict.get('error_message', 'unknown format')}).")
        body_list.append(body_bytes)
        offset_int += len(body_dict[list_key_str_in])
        # STOP WHEN EVERY ROW HAS BEEN READ (OR A PAGE IS EMPTY)
        if not body_dict[list_key_str_in] or offset_int >= int(body_dict.get("count", 0)):
            break
    return body_list

# FUNCTION: FETCH EVERY REAL-TIME PERIOD OF A SERIES, IN WINDOWS OF AT MOST VINTAGE_WINDOW_INT VINTAGE DATES
def fetch_fred_page_list(series_str_in, getter_in=None):
    """
    Args:
        series_str_in (str): FRED series id
        getter_in (callable | None): (endpoint, query dict) -> bytes; None = the real API with the stored key (tests pass a fake)

    Returns:
        list[bytes]: Response bodies: the vintage-date pages first, then the observation pages of each window
    """
    # USE THE REAL API UNLESS A GETTER IS GIVEN
    if getter_in is None:
        api_key_str = get_fred_api_key_str()
        getter_in = lambda endpoint_str, query_dict: get_fred_response_bytes(endpoint_str, query_dict, api_key_str)
    # LIST EVERY VINTAGE DATE OF THE SERIES
    vintage_body_list = get_paged_body_list(getter_in, "series/vintagedates", {"series_id": series_str_in, "realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}, "vintage_dates")
    vintage_str_list = sorted({date_str for body_bytes in vintage_body_list for date_str in json.loads(body_bytes.decode("utf-8"))["vintage_dates"]})
    if not vintage_str_list:
        raise FredError(f"FRED lists no vintage dates for {series_str_in}.")
    # ONE OBSERVATION QUERY PER WINDOW (THE API REFUSES MORE THAN 2000 VINTAGE DATES IN ONE JSON REQUEST); PERIODS ARE CLIPPED TO
    # THE WINDOW, SO A PERIOD CROSSING A WINDOW EDGE COMES BACK ONCE PER WINDOW WITH THE SAME VALUE (get_vintage_row_pdf DROPS THE REPEATS)
    observation_body_list = []
    for start_int in range(0, len(vintage_str_list), VINTAGE_WINDOW_INT):
        window_str_list = vintage_str_list[start_int:start_int + VINTAGE_WINDOW_INT]
        query_dict = {"series_id": series_str_in, "output_type": 1, "sort_order": "asc", "realtime_start": window_str_list[0],
                      "realtime_end": "9999-12-31" if start_int + VINTAGE_WINDOW_INT >= len(vintage_str_list) else window_str_list[-1]}
        observation_body_list += get_paged_body_list(getter_in, "series/observations", query_dict, "observations")
    # RETURN THE BODIES (VINTAGE LIST FIRST)
    return vintage_body_list + observation_body_list

# FUNCTION: PARSE THE PAGES INTO REAL-TIME PERIODS
def get_period_pdf(page_bytes_list_in):
    """
    Args:
        page_bytes_list_in (list[bytes]): Response bodies

    Returns:
        pd.DataFrame: observation_date, realtime_start, realtime_end, value (all text), one row per real-time period
    """
    # COLLECT THE OBSERVATIONS OF EVERY PAGE
    row_list = [row for page_bytes in page_bytes_list_in for row in json.loads(page_bytes.decode("utf-8")).get("observations", [])]
    period_pdf = pd.DataFrame(row_list, columns=["realtime_start", "realtime_end", "date", "value"]).astype(str)
    # RENAME THE OBSERVATION DATE AND SORT
    period_pdf = period_pdf.rename(columns={"date": "observation_date"})
    return period_pdf.sort_values(["observation_date", "realtime_start"]).reset_index(drop=True)

# FUNCTION: GET THE NEXT US FEDERAL BUSINESS DAY AFTER EACH DATE
def get_next_federal_business_day_series(date_series_in):
    """
    Args:
        date_series_in (pd.Series): Dates (text YYYY-MM-DD)

    Returns:
        pd.Series: The next weekday that is not a US federal holiday, as text
    """
    # ROLL EACH DATE BACK TO A BUSINESS DAY, THEN STEP ONE BUSINESS DAY FORWARD (WEEKENDS AND FEDERAL HOLIDAYS SKIPPED)
    date_array = pd.to_datetime(date_series_in).values.astype("datetime64[D]")
    holiday_array = USFederalHolidayCalendar().holidays("1900-01-01", "2100-12-31").values.astype("datetime64[D]")
    next_array = np.busday_offset(date_array, 1, roll="backward", holidays=holiday_array)
    return pd.Series(pd.to_datetime(next_array).strftime("%Y-%m-%d"), index=date_series_in.index)

# FUNCTION: FORMAT AN AVAILABILITY TIME (A DATE AT 16:30 NEW YORK, WITH ITS OFFSET)
def get_available_ts_series(date_series_in):
    """
    Args:
        date_series_in (pd.Series): Dates (text YYYY-MM-DD)

    Returns:
        pd.Series: e.g. "2005-01-04 16:30:00-05:00"
    """
    # LOCALISE EACH DATE AT 16:30 IN NEW YORK AND FORMAT WITH A COLON IN THE OFFSET (AS THE BAR TIMESTAMPS)
    ts_series = pd.to_datetime(date_series_in + " " + AVAILABLE_TIME_STR).dt.tz_localize(config.NY_TZ_STR)
    return ts_series.map(lambda ts: ts.isoformat(sep=" "))

# FUNCTION: BUILD THE PUBLISHED ROWS FROM THE REAL-TIME PERIODS
def get_vintage_row_pdf(period_pdf_in, run_id_str_in):
    """
    Args:
        period_pdf_in (pd.DataFrame): Output of get_period_pdf
        run_id_str_in (str): Run id written into new rows

    Returns:
        pd.DataFrame: Rows in FRED_COLUMN_STR_LIST order (first_seen / rule_based_backfill / revision), sorted by the key
    """
    # NOTHING TO BUILD FROM NO PERIODS
    if period_pdf_in.empty:
        return pd.DataFrame(columns=FRED_COLUMN_STR_LIST)
    period_pdf = period_pdf_in.sort_values(["observation_date", "realtime_start"]).reset_index(drop=True)
    # KEEP THE FIRST PERIOD OF EACH OBSERVATION AND EVERY LATER PERIOD WHOSE VALUE CHANGED
    previous_value_series = period_pdf.groupby("observation_date")["value"].shift()
    first_bool_series = previous_value_series.isna()
    keep_pdf = period_pdf[first_bool_series | (period_pdf["value"] != previous_value_series)].copy()
    keep_first_bool_series = first_bool_series[keep_pdf.index]
    # THE SERIES' EARLIEST VINTAGE: AN OBSERVATION OLDER THAN IT HAS NO RECORD OF ITS FIRST POSTING
    earliest_vintage_str = period_pdf["realtime_start"].min()
    backfill_bool_series = keep_first_bool_series & (keep_pdf["realtime_start"] == earliest_vintage_str) & (keep_pdf["observation_date"] < earliest_vintage_str)
    # LABEL THE ROWS
    keep_pdf["vintage_kind"] = "revision"
    keep_pdf.loc[keep_first_bool_series, "vintage_kind"] = "first_seen"
    keep_pdf.loc[backfill_bool_series, "vintage_kind"] = "rule_based_backfill"
    # AVAILABILITY DATE: THE REALTIME START, OR THE NEXT FEDERAL BUSINESS DAY FOR BACKFILLED ROWS
    available_date_series = keep_pdf["realtime_start"].copy()
    if backfill_bool_series.any():
        available_date_series[backfill_bool_series] = get_next_federal_business_day_series(keep_pdf.loc[backfill_bool_series, "observation_date"]).values
    # BUILD THE PUBLISHED COLUMNS
    row_pdf = pd.DataFrame({"date": available_date_series, "available_ts": get_available_ts_series(available_date_series),
                            "observation_date": keep_pdf["observation_date"], "value_pct": keep_pdf["value"],
                            "vintage_realtime_start": keep_pdf["realtime_start"], "vintage_kind": keep_pdf["vintage_kind"], "ingest_run_id": run_id_str_in})
    # RETURN THE ROWS SORTED BY THE KEY
    return row_pdf.sort_values(FRED_KEY_STR_LIST).reset_index(drop=True)[FRED_COLUMN_STR_LIST]

# FUNCTION: CHECK THE ROWS
def get_fred_issue_dict(row_pdf_in, spec_dict_in, today_str_in):
    """
    Args:
        row_pdf_in (pd.DataFrame): Output of get_vintage_row_pdf
        spec_dict_in (dict): Entry of FRED_SERIES_SPEC_DICT
        today_str_in (str): Retrieval date (YYYY-MM-DD, UTC)

    Returns:
        dict: {check name: list of offending observation dates}; "blocking" checks stop a publish, the others are reported
    """
    # PREPARE THE VALUES ("." = NO VALUE)
    value_series = pd.to_numeric(row_pdf_in["value_pct"].where(row_pdf_in["value_pct"] != "."), errors="coerce")
    first_pdf = row_pdf_in[row_pdf_in["vintage_kind"] != "revision"]
    issue_dict = {}
    # BLOCKING: ONE ROW PER KEY; ONE FIRST ROW PER OBSERVATION; NO DATE IN THE FUTURE; VALUES PARSE AND ARE IN RANGE
    issue_dict["blocking_duplicate_key"] = row_pdf_in.loc[row_pdf_in.duplicated(FRED_KEY_STR_LIST, keep=False), "observation_date"].tolist()
    issue_dict["blocking_first_row_count"] = first_pdf.loc[first_pdf.duplicated("observation_date", keep=False), "observation_date"].tolist()
    issue_dict["blocking_observation_after_today"] = row_pdf_in.loc[row_pdf_in["observation_date"] > today_str_in, "observation_date"].tolist()
    issue_dict["blocking_vintage_after_today"] = row_pdf_in.loc[row_pdf_in["vintage_realtime_start"] > today_str_in, "observation_date"].tolist()
    issue_dict["blocking_available_before_observation"] = row_pdf_in.loc[row_pdf_in["date"] <= row_pdf_in["observation_date"], "observation_date"].tolist()
    issue_dict["blocking_unparsable_value"] = row_pdf_in.loc[value_series.isna() & (row_pdf_in["value_pct"] != "."), "observation_date"].tolist()
    in_range_bool_series = value_series.between(spec_dict_in["value_min_float"], spec_dict_in["value_max_float"])
    issue_dict["blocking_value_out_of_range"] = row_pdf_in.loc[value_series.notna() & ~in_range_bool_series, "observation_date"].tolist()
    # REPORTED: OBSERVATIONS ON A WEEKEND OR FEDERAL HOLIDAY; "." VALUES; WEEKDAYS WITH NO OBSERVATION (FEDERAL HOLIDAYS ARE EXPECTED)
    if spec_dict_in["business_day_bool"] and not first_pdf.empty:
        observation_ts_series = pd.to_datetime(first_pdf["observation_date"])
        holiday_set = set(USFederalHolidayCalendar().holidays(observation_ts_series.min(), observation_ts_series.max()).strftime("%Y-%m-%d"))
        not_business_bool_series = (observation_ts_series.dt.dayofweek >= 5) | first_pdf["observation_date"].isin(holiday_set)
        issue_dict["reported_observation_not_business_day"] = first_pdf.loc[not_business_bool_series.values, "observation_date"].tolist()
        weekday_str_set = set(pd.bdate_range(observation_ts_series.min(), observation_ts_series.max()).strftime("%Y-%m-%d"))
        missing_str_set = weekday_str_set - set(first_pdf["observation_date"])
        issue_dict["reported_weekday_missing_not_holiday"] = sorted(missing_str_set - holiday_set)
        issue_dict["reported_weekday_missing_holiday"] = sorted(missing_str_set & holiday_set)
    issue_dict["reported_dot_value_first_row"] = first_pdf.loc[first_pdf["value_pct"] == ".", "observation_date"].tolist()
    # COUNT THE OBSERVATIONS WHOSE FIRST VALUE DIFFERS FROM THE LATEST
    latest_value_series = row_pdf_in.groupby("observation_date")["value_pct"].last()
    first_value_series = first_pdf.set_index("observation_date")["value_pct"]
    issue_dict["reported_first_differs_from_latest"] = first_value_series.index[first_value_series != latest_value_series.reindex(first_value_series.index)].tolist()
    # RETURN THE ISSUES
    return issue_dict

# FUNCTION: SUMMARISE ROWS AND ISSUES WITHOUT VALUES OR RECENT DATES
def get_fred_summary_dict(row_pdf_in, issue_dict_in, print_dates_before_str_in):
    """
    Args:
        row_pdf_in (pd.DataFrame): Published rows built this run
        issue_dict_in (dict): Output of get_fred_issue_dict
        print_dates_before_str_in (str): Only dates before this one may be shown

    Returns:
        dict: Counts, and for each issue its count plus the dates before the cut-off (at most 10)
    """
    # COUNT THE ROWS BY KIND
    summary_dict = {"row_count_int": int(len(row_pdf_in)), "kind_count_dict": {key_str: int(count) for key_str, count in row_pdf_in["vintage_kind"].value_counts().items()},
                    "observation_count_int": int(row_pdf_in["observation_date"].nunique())}
    # ADD EACH ISSUE: ITS COUNT, AND ONLY DATES BEFORE THE CUT-OFF
    for name_str, date_list in issue_dict_in.items():
        shown_list = sorted({date_str for date_str in date_list if date_str < print_dates_before_str_in})[:10]
        summary_dict[name_str] = {"count_int": len(date_list), f"dates_before_{print_dates_before_str_in}": shown_list}
    # RETURN THE SUMMARY
    return summary_dict

# FUNCTION: READ THE PUBLISHED FILE
def read_published_pdf(file_path_str_in):
    """
    Args:
        file_path_str_in (str): Published CSV

    Returns:
        pd.DataFrame: Its rows as text (empty with the columns if there is no file); raises if the header differs
    """
    # NO FILE YET
    if not os.path.exists(file_path_str_in):
        return pd.DataFrame(columns=FRED_COLUMN_STR_LIST)
    # READ AS TEXT AND CHECK THE HEADER
    published_pdf = pd.read_csv(file_path_str_in, dtype=str, keep_default_na=False)
    if list(published_pdf.columns) != FRED_COLUMN_STR_LIST:
        raise FredError(f"{file_path_str_in} has other columns than contract version 2; nothing written.")
    return published_pdf

# FUNCTION: COMPARE NEW ROWS WITH THE PUBLISHED ROWS (APPEND-ONLY)
def get_append_tuple(published_pdf_in, row_pdf_in):
    """
    Args:
        published_pdf_in (pd.DataFrame): Rows already published
        row_pdf_in (pd.DataFrame): Rows built this run

    Returns:
        tuple: (rows to add, keys published with another value or kind, count of published keys this run did not see)
    """
    # MATCH ON THE KEY
    merge_pdf = row_pdf_in.merge(published_pdf_in, on=FRED_KEY_STR_LIST, how="left", suffixes=("", "_published"), indicator=True)
    # NEW KEYS ARE ADDED
    add_pdf = row_pdf_in[(merge_pdf["_merge"] == "left_only").values]
    # A KNOWN KEY WITH ANOTHER VALUE OR KIND IS A CONFLICT
    both_pdf = merge_pdf[merge_pdf["_merge"] == "both"]
    conflict_bool_series = (both_pdf["value_pct"] != both_pdf["value_pct_published"]) | (both_pdf["vintage_kind"] != both_pdf["vintage_kind_published"])
    conflict_list = both_pdf.loc[conflict_bool_series, "observation_date"].tolist()
    # PUBLISHED KEYS THE SOURCE NO LONGER SENDS (KEPT; REPORTED)
    unseen_int = len(published_pdf_in) - int((merge_pdf["_merge"] == "both").sum())
    # RETURN THE RESULT
    return add_pdf, conflict_list, unseen_int

# FUNCTION: SAVE THE DOWNLOADED PAGES
def save_page_list(page_bytes_list_in, folder_path_str_in):
    """
    Args:
        page_bytes_list_in (list[bytes]): Response bodies
        folder_path_str_in (str): Folder (created)

    Returns:
        list[dict]: File name and SHA-256 per page
    """
    # WRITE EACH PAGE AS DOWNLOADED, THEN HASH IT
    os.makedirs(folder_path_str_in, exist_ok=True)
    file_list = []
    for index_int, page_bytes in enumerate(page_bytes_list_in):
        file_path_str = f"{folder_path_str_in}page_{index_int:03d}.json"
        with open(file_path_str, "wb") as file:
            file.write(page_bytes)
        file_list.append({"file_str": os.path.basename(file_path_str), "sha256_str": get_file_sha256_str(file_path_str)})
    # RETURN THE LIST
    return file_list

# FUNCTION: RUN ONE SERIES (FETCH, BUILD, CHECK, AND PUBLISH WHEN apply)
def run_fred_series_dict(series_str_in, apply_bool_in, run_id_str_in, page_getter_in=None, today_str_in=None):
    """
    Args:
        series_str_in (str): Key of FRED_SERIES_SPEC_DICT
        apply_bool_in (bool): False = dry run (nothing written)
        run_id_str_in (str): Run id
        page_getter_in (callable | None): Fake (endpoint, query) getter for tests
        today_str_in (str | None): Retrieval date (None = today in UTC)

    Returns:
        dict: Summary (counts only); raises FredError when a blocking check fails or a published row would change
    """
    # PATHS AND DATES
    spec_dict = FRED_SERIES_SPEC_DICT[series_str_in]
    folder_path_str = f"{config.RAWZONE_PATH_STR}{spec_dict['folder_str']}/"
    file_path_str = f"{folder_path_str}{spec_dict['file_str']}"
    today_str = today_str_in or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # FETCH, PARSE, BUILD AND CHECK
    page_bytes_list = fetch_fred_page_list(series_str_in, page_getter_in)
    row_pdf = get_vintage_row_pdf(get_period_pdf(page_bytes_list), run_id_str_in)
    issue_dict = get_fred_issue_dict(row_pdf, spec_dict, today_str)
    summary_dict = get_fred_summary_dict(row_pdf, issue_dict, config.PRINT_DATES_BEFORE_STR)
    summary_dict["page_count_int"] = len(page_bytes_list)
    blocking_list = [name_str for name_str, date_list in issue_dict.items() if name_str.startswith("blocking") and date_list]
    # COMPARE WITH WHAT IS PUBLISHED
    published_pdf = read_published_pdf(file_path_str)
    add_pdf, conflict_list, unseen_int = get_append_tuple(published_pdf, row_pdf)
    summary_dict.update({"published_row_count_int": int(len(published_pdf)), "row_to_add_count_int": int(len(add_pdf)),
                         "conflict_count_int": len(conflict_list), "published_unseen_count_int": unseen_int})
    # STOP ON A BLOCKING CHECK OR A CONFLICT (NOTHING IS WRITTEN)
    if blocking_list or conflict_list:
        summary_dict["stopped_str"] = f"blocking checks: {blocking_list}; conflicts with published rows: {len(conflict_list)}"
        return summary_dict
    # DRY RUN: STOP HERE
    if not apply_bool_in:
        return summary_dict
    # SAVE THE PAGES AS DOWNLOADED
    download_path_str = f"{config.INGEST_RECORD_PATH_STR}downloads/{spec_dict['folder_str']}/{run_id_str_in}/"
    summary_dict["download_file_list"] = save_page_list(page_bytes_list, download_path_str)
    # NOTHING NEW: LEAVE THE PUBLISHED FILE AS IT IS
    if add_pdf.empty:
        return summary_dict
    # BACK UP THE PUBLISHED FILE, THEN WRITE THE UNION ATOMICALLY (PUBLISHED ROWS UNCHANGED)
    os.makedirs(folder_path_str, exist_ok=True)
    if os.path.exists(file_path_str):
        backup_path_str = f"{config.RAWZONE_PATH_STR}{spec_dict['folder_str']}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}/"
        os.makedirs(backup_path_str, exist_ok=True)
        shutil.copy2(file_path_str, backup_path_str)
    union_pdf = pd.concat([published_pdf, add_pdf], ignore_index=True).sort_values(FRED_KEY_STR_LIST)[FRED_COLUMN_STR_LIST]
    temporary_path_str = f"{file_path_str}.tmp"
    union_pdf.to_csv(temporary_path_str, index=False, lineterminator="\n")
    os.replace(temporary_path_str, file_path_str)
    summary_dict["published_sha256_str"] = get_file_sha256_str(file_path_str)
    # RETURN THE SUMMARY
    return summary_dict
