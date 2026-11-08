import os
import sys
import json
import glob
import tempfile
import subprocess

# POINT THE INGEST TO A TEMPORARY TARGET ROOT BEFORE IT IS IMPORTED (NEVER A REAL DATA FOLDER)
TEST_ROOT_PATH_STR = tempfile.mkdtemp(prefix="so_fred_test_").replace("\\", "/") + "/"
os.makedirs(f"{TEST_ROOT_PATH_STR}store01_rawzone", exist_ok=True)
os.environ["SO_INGEST_TARGET_ROOT"] = TEST_ROOT_PATH_STR
REPO_PATH_STR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_PATH_STR)
import pandas as pd
from ingest import config, fred

"""
FRED / ALFRED Vintage Pipeline (synthetic pages written by this test, in the API's JSON format; no network, no key)

    python tests/test_fred.py
"""

# FUNCTION: PRINT A PASSED TEST
def passed(name_str_in):
    print(f"✅ {name_str_in}")

# DEFINE SYNTHETIC REAL-TIME PERIODS (realtime_start, realtime_end, observation date, value)
#   the earliest vintage is 2005-01-03: observations before it are backfilled by rule
PERIOD_TUPLE_LIST = [
    ("2005-01-03", "9999-12-31", "2004-12-30", "2.20"),          # older than the earliest vintage -> rule_based_backfill (next federal business day 2004-12-31)
    ("2005-01-03", "9999-12-31", "2004-12-31", "2.21"),          # -> backfill, next federal business day 2005-01-03
    ("2005-01-04", "2005-01-10", "2005-01-03", "2.25"),          # first_seen 2005-01-04
    ("2005-01-11", "9999-12-31", "2005-01-03", "2.26"),          # value changed -> revision 2005-01-11
    ("2005-01-05", "2005-01-06", "2005-01-04", "2.27"),          # first_seen
    ("2005-01-07", "9999-12-31", "2005-01-04", "2.27"),          # same value, new period -> not a row
    ("2005-01-06", "2005-01-12", "2005-01-05", "."),             # no value first ...
    ("2005-01-13", "9999-12-31", "2005-01-05", "2.30"),          # ... then a value -> revision
]

# FUNCTION: BUILD A FAKE PAGE GETTER (PAGES OF page_size ROWS, IN THE API'S JSON SHAPE)
def get_fake_getter(period_tuple_list_in, page_size_int_in=3, call_list_in=None):
    row_list = [{"realtime_start": start_str, "realtime_end": end_str, "date": date_str, "value": value_str}
                for start_str, end_str, date_str, value_str in period_tuple_list_in]
    def getter(series_str_in, offset_int_in):
        call_list_in.append(offset_int_in) if call_list_in is not None else None
        page_dict = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "output_type": 1, "count": len(row_list),
                     "offset": offset_int_in, "limit": page_size_int_in, "observations": row_list[offset_int_in:offset_int_in + page_size_int_in]}
        return json.dumps(page_dict).encode("utf-8")
    return getter

# TEST: PAGING AND PARSING
def test_paging():
    call_list = []
    page_list = fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST, 3, call_list))
    assert call_list == [0, 3, 6], call_list
    period_pdf = fred.get_period_pdf(page_list)
    assert len(period_pdf) == len(PERIOD_TUPLE_LIST)
    passed("paging and parsing")

# TEST: VINTAGE ROWS (KINDS, CLOCKS, DEDUPLICATION)
def test_vintage_rows():
    row_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST))), "RUN1")
    assert list(row_pdf.columns) == fred.FRED_COLUMN_STR_LIST
    kind_dict = {(row.observation_date, row.vintage_realtime_start): row.vintage_kind for row in row_pdf.itertuples()}
    assert kind_dict == {("2004-12-30", "2005-01-03"): "rule_based_backfill", ("2004-12-31", "2005-01-03"): "rule_based_backfill",
                         ("2005-01-03", "2005-01-04"): "first_seen", ("2005-01-03", "2005-01-11"): "revision",
                         ("2005-01-04", "2005-01-05"): "first_seen", ("2005-01-05", "2005-01-06"): "first_seen",
                         ("2005-01-05", "2005-01-13"): "revision"}, kind_dict
    date_dict = {(row.observation_date, row.vintage_realtime_start): (row.date, row.available_ts) for row in row_pdf.itertuples()}
    # BACKFILL: THE NEXT FEDERAL BUSINESS DAY AT 16:30 NEW YORK (FRIDAY 2004-12-31 IS THE OBSERVED NEW YEAR HOLIDAY)
    assert date_dict[("2004-12-30", "2005-01-03")] == ("2005-01-03", "2005-01-03 16:30:00-05:00")
    assert date_dict[("2004-12-31", "2005-01-03")] == ("2005-01-03", "2005-01-03 16:30:00-05:00")
    assert date_dict[("2005-01-03", "2005-01-11")] == ("2005-01-11", "2005-01-11 16:30:00-05:00")
    # SUMMER TIME OFFSET
    assert fred.get_available_ts_series(pd.Series(["2005-07-05"])).iloc[0] == "2005-07-05 16:30:00-04:00"
    # A FEDERAL HOLIDAY IS SKIPPED BY THE RULE (2005-01-17 MARTIN LUTHER KING DAY)
    assert fred.get_next_federal_business_day_series(pd.Series(["2005-01-14", "2005-01-17", "2005-01-15"])).tolist() == ["2005-01-18"] * 3
    # VALUES STAY AS SENT
    assert set(row_pdf["value_pct"]) == {"2.20", "2.21", "2.25", "2.26", "2.27", ".", "2.30"}
    passed("vintage rows: kinds, 16:30 clocks, backfill rule, unchanged periods dropped, '.' kept")

# TEST: CHECKS
def test_checks():
    spec_dict = fred.FRED_SERIES_SPEC_DICT["DTB3"]
    row_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST))), "RUN1")
    issue_dict = fred.get_fred_issue_dict(row_pdf, spec_dict, "2026-01-01")
    assert not any(date_list for name_str, date_list in issue_dict.items() if name_str.startswith("blocking")), issue_dict
    assert issue_dict["reported_dot_value_first_row"] == ["2005-01-05"]
    assert sorted(issue_dict["reported_first_differs_from_latest"]) == ["2005-01-03", "2005-01-05"]
    # FUTURE, OUT OF RANGE AND UNPARSABLE VALUES BLOCK
    bad_list = PERIOD_TUPLE_LIST + [("2005-01-03", "9999-12-31", "2027-01-04", "2.0"), ("2005-01-10", "9999-12-31", "2005-01-07", "25.0"),
                                    ("2005-01-11", "9999-12-31", "2005-01-10", "n/a")]
    bad_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(fred.fetch_fred_page_list("DTB3", get_fake_getter(bad_list))), "RUN1")
    bad_issue_dict = fred.get_fred_issue_dict(bad_pdf, spec_dict, "2026-01-01")
    assert bad_issue_dict["blocking_observation_after_today"] == ["2027-01-04"]
    assert bad_issue_dict["blocking_value_out_of_range"] == ["2005-01-07"]
    assert bad_issue_dict["blocking_unparsable_value"] == ["2005-01-10"]
    # THE SUMMARY NEVER SHOWS A DATE ON OR AFTER THE CUT-OFF, AND HOLDS NO VALUE
    summary_str = json.dumps(fred.get_fred_summary_dict(bad_pdf, bad_issue_dict, "2005-01-05"))
    assert "2027-01-04" not in summary_str and "2005-01-07" not in summary_str and "2005-01-10" not in summary_str
    assert "25.0" not in summary_str and "2.26" not in summary_str
    passed("checks: blocking and reported; summary shows counts and only dates before the cut-off")

# TEST: PUBLISH (DRY RUN, APPLY, RERUN, NEW VINTAGE, CONFLICT)
def test_publish():
    file_path_str = f"{config.RAWZONE_PATH_STR}fred_dtb3/dtb3.csv"
    # DRY RUN WRITES NOTHING
    summary_dict = fred.run_fred_series_dict("DTB3", False, "RUN1", get_fake_getter(PERIOD_TUPLE_LIST), "2026-01-01")
    assert summary_dict["row_to_add_count_int"] == 7 and not os.path.exists(file_path_str)
    assert not os.path.exists(f"{config.INGEST_RECORD_PATH_STR}downloads")
    # APPLY WRITES THE FILE AND THE PAGES
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN1", get_fake_getter(PERIOD_TUPLE_LIST), "2026-01-01")
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert len(published_pdf) == 7 and list(published_pdf.columns) == fred.FRED_COLUMN_STR_LIST
    assert len(glob.glob(f"{config.INGEST_RECORD_PATH_STR}downloads/fred_dtb3/RUN1/page_*.json")) == 3
    first_bytes = open(file_path_str, "rb").read()
    # A RERUN ADDS NOTHING AND LEAVES THE FILE BYTE-FOR-BYTE
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN2", get_fake_getter(PERIOD_TUPLE_LIST), "2026-01-01")
    assert summary_dict["row_to_add_count_int"] == 0 and open(file_path_str, "rb").read() == first_bytes
    # A NEW VINTAGE ADDS ONE REVISION ROW; EARLIER ROWS KEEP THEIR RUN ID
    new_list = PERIOD_TUPLE_LIST + [("2005-02-01", "9999-12-31", "2005-01-04", "2.28")]
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN3", get_fake_getter(new_list), "2026-01-01")
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert summary_dict["row_to_add_count_int"] == 1 and len(published_pdf) == 8
    assert published_pdf["ingest_run_id"].value_counts().to_dict() == {"RUN1": 7, "RUN3": 1}
    assert glob.glob(f"{config.RAWZONE_PATH_STR}fred_dtb3_backup_*/dtb3.csv")
    # A SOURCE THAT CHANGES A PUBLISHED VINTAGE STOPS THE RUN; THE FILE IS UNTOUCHED
    before_bytes = open(file_path_str, "rb").read()
    changed_list = [(s, e, d, "2.99" if (d, s) == ("2005-01-03", "2005-01-04") else v) for s, e, d, v in new_list]
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN4", get_fake_getter(changed_list), "2026-01-01")
    assert summary_dict["conflict_count_int"] == 1 and "stopped_str" in summary_dict
    assert open(file_path_str, "rb").read() == before_bytes
    # A PUBLISHED FILE WITH OTHER COLUMNS IS REFUSED
    other_path_str = f"{TEST_ROOT_PATH_STR}other.csv"
    pd.DataFrame({"a": [1]}).to_csv(other_path_str, index=False)
    try:
        fred.read_published_pdf(other_path_str)
        raise AssertionError("other columns accepted")
    except fred.FredError:
        pass
    passed("publish: dry run writes nothing, append-only, rerun byte-identical, new vintage added, conflict stops, backup made")

# TEST: ERRORS NEVER CARRY THE KEY
def test_key_never_shown():
    secret_str = "abcdefghij0123456789abcdefghij01"
    def failing_getter(series_str_in, offset_int_in):
        return json.dumps({"error_code": 400, "error_message": f"Bad Request. api_key {secret_str} is not registered"}).encode("utf-8")
    try:
        fred.fetch_fred_page_list("DTB3", failing_getter)
        raise AssertionError("no error")
    except fred.FredError as error:
        message_str = str(error)
    # THE GETTER'S MESSAGE IS SHOWN AS FRED SENT IT HERE (A FAKE); THE REAL GETTER MASKS THE KEY: CHECK THAT PATH
    import urllib.error, io
    from unittest import mock
    http_error = urllib.error.HTTPError("https://x?api_key=" + secret_str, 400, "Bad Request", {}, io.BytesIO(json.dumps({"error_message": f"key {secret_str} bad"}).encode()))
    with mock.patch("urllib.request.urlopen", side_effect=http_error):
        try:
            fred.get_fred_page_bytes("DTB3", 0, secret_str)
        except fred.FredError as error:
            assert secret_str not in str(error) and "HTTP 400" in str(error), str(error)
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(f"https://x?api_key={secret_str}")):
        try:
            fred.get_fred_page_bytes("DTB3", 0, secret_str)
        except fred.FredError as error:
            assert secret_str not in str(error), str(error)
    passed("API errors never carry the key")

# TEST: THE SCRIPT (DRY RUN WITHOUT A KEY STOPS CLEANLY; ROOT CHECK)
def test_script():
    env_dict = {**os.environ, "SO_INGEST_TARGET_ROOT": TEST_ROOT_PATH_STR, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": REPO_PATH_STR,
                "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    result = subprocess.run([sys.executable, f"{REPO_PATH_STR}/scripts/ingest/download_fred_series.py"], env=env_dict, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 1 and "No FRED API key" in result.stdout, result.stdout + result.stderr
    env_dict["SO_INGEST_TARGET_ROOT"] = tempfile.mkdtemp(prefix="so_fred_noraw_")
    result = subprocess.run([sys.executable, f"{REPO_PATH_STR}/scripts/ingest/download_fred_series.py"], env=env_dict, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 1 and "store01_rawzone" in result.stdout + result.stderr, result.stdout + result.stderr
    passed("script: no key -> clean stop; target root check")

# RUN THE TESTS
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_paging()
    test_vintage_rows()
    test_checks()
    test_publish()
    test_key_never_shown()
    test_script()
    print("\nAll FRED tests passed ✅")
