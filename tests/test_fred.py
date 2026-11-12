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
    ("2005-01-06", "9999-12-31", "2005-01-06", "2.31"),          # ALFRED records the posting ON the observation date -> moved to 2005-01-07
    ("2005-01-08", "9999-12-31", "2005-01-07", "2.32"),          # posted on a Saturday -> moved to Monday 2005-01-10
]

# FUNCTION: BUILD A FAKE FRED API (vintagedates; observations clipped to the requested real-time window, as FRED does; pages of page_size)
def get_fake_getter(period_tuple_list_in, page_size_int_in=3, call_list_in=None):
    def getter(endpoint_str_in, query_dict_in):
        call_list_in.append((endpoint_str_in, query_dict_in.get("realtime_start"), query_dict_in["offset"])) if call_list_in is not None else None
        offset_int = query_dict_in["offset"]
        if endpoint_str_in == "series/vintagedates":
            vintage_list = sorted({start_str for start_str, _, _, _ in period_tuple_list_in})
            return json.dumps({"count": len(vintage_list), "vintage_dates": vintage_list[offset_int:offset_int + page_size_int_in]}).encode("utf-8")
        window_start_str, window_end_str = query_dict_in["realtime_start"], query_dict_in["realtime_end"]
        row_list = []
        for start_str, end_str, date_str, value_str in period_tuple_list_in:
            if start_str <= window_end_str and end_str >= window_start_str:
                row_list.append({"realtime_start": max(start_str, window_start_str), "realtime_end": min(end_str, window_end_str), "date": date_str, "value": value_str})
        row_list.sort(key=lambda row: (row["date"], row["realtime_start"]))
        return json.dumps({"count": len(row_list), "offset": offset_int, "observations": row_list[offset_int:offset_int + page_size_int_in]}).encode("utf-8")
    return getter

# TEST: PAGING, WINDOWS AND PARSING
def test_paging():
    call_list = []
    page_list = fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST, 3, call_list))
    # 8 VINTAGE DATES IN PAGES OF 3; ONE WINDOW (WINDOW SIZE 1000); 10 PERIODS IN PAGES OF 3
    assert [call for call in call_list if call[0] == "series/vintagedates"] == [("series/vintagedates", "1776-07-04", 0), ("series/vintagedates", "1776-07-04", 3), ("series/vintagedates", "1776-07-04", 6)], call_list
    assert len(fred.get_period_pdf(page_list)) == len(PERIOD_TUPLE_LIST)
    # SMALL WINDOWS: SEVERAL OBSERVATION QUERIES, CLIPPED REPEATS, SAME PUBLISHED ROWS AS ONE WINDOW
    one_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(page_list), "RUN1")
    saved_int, fred.VINTAGE_WINDOW_INT = fred.VINTAGE_WINDOW_INT, 2
    try:
        call_list = []
        window_list = fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST, 3, call_list))
    finally:
        fred.VINTAGE_WINDOW_INT = saved_int
    window_start_list = sorted({call[1] for call in call_list if call[0] == "series/observations"})
    assert window_start_list == ["2005-01-03", "2005-01-05", "2005-01-07", "2005-01-11"], window_start_list
    assert len(fred.get_period_pdf(window_list)) > len(PERIOD_TUPLE_LIST)
    window_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(window_list), "RUN1")
    assert window_pdf.equals(one_pdf), (window_pdf, one_pdf)
    passed("paging, vintage-date windows (clipped repeats collapse to the same rows) and parsing")

# TEST: VINTAGE ROWS (KINDS, CLOCKS, DEDUPLICATION)
def test_vintage_rows():
    row_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST))), "RUN1")
    assert list(row_pdf.columns) == fred.FRED_COLUMN_STR_LIST
    kind_dict = {(row.observation_date, row.vintage_realtime_start): row.vintage_kind for row in row_pdf.itertuples()}
    assert kind_dict == {("2004-12-30", "2005-01-03"): "rule_based_backfill", ("2004-12-31", "2005-01-03"): "rule_based_backfill",
                         ("2005-01-03", "2005-01-04"): "first_seen", ("2005-01-03", "2005-01-11"): "revision",
                         ("2005-01-04", "2005-01-05"): "first_seen", ("2005-01-05", "2005-01-06"): "first_seen",
                         ("2005-01-05", "2005-01-13"): "revision", ("2005-01-06", "2005-01-06"): "first_seen",
                         ("2005-01-07", "2005-01-08"): "first_seen"}, kind_dict
    date_dict = {(row.observation_date, row.vintage_realtime_start): (row.date, row.available_ts) for row in row_pdf.itertuples()}
    # BACKFILL: THE NEXT FEDERAL BUSINESS DAY AT 16:30 NEW YORK (FRIDAY 2004-12-31 IS THE OBSERVED NEW YEAR HOLIDAY)
    assert date_dict[("2004-12-30", "2005-01-03")] == ("2005-01-03", "2005-01-03 16:30:00-05:00")
    assert date_dict[("2004-12-31", "2005-01-03")] == ("2005-01-03", "2005-01-03 16:30:00-05:00")
    assert date_dict[("2005-01-03", "2005-01-11")] == ("2005-01-11", "2005-01-11 16:30:00-05:00")
    # NEVER EARLIER THAN THE NEXT FEDERAL BUSINESS DAY AFTER THE OBSERVATION; THE ALFRED DATE IS KEPT IN vintage_realtime_start
    assert date_dict[("2005-01-06", "2005-01-06")] == ("2005-01-07", "2005-01-07 16:30:00-05:00")
    assert date_dict[("2005-01-07", "2005-01-08")] == ("2005-01-10", "2005-01-10 16:30:00-05:00")
    assert date_dict[("2005-01-03", "2005-01-04")] == ("2005-01-04", "2005-01-04 16:30:00-05:00")
    # SUMMER TIME OFFSET
    assert fred.get_available_ts_series(pd.Series(["2005-07-05"])).iloc[0] == "2005-07-05 16:30:00-04:00"
    # A FEDERAL HOLIDAY IS SKIPPED BY THE RULE (2005-01-17 MARTIN LUTHER KING DAY)
    assert fred.get_next_federal_business_day_series(pd.Series(["2005-01-14", "2005-01-17", "2005-01-15"])).tolist() == ["2005-01-18"] * 3
    # VALUES STAY AS SENT
    assert set(row_pdf["value_pct"]) == {"2.20", "2.21", "2.25", "2.26", "2.27", ".", "2.30", "2.31", "2.32"}
    passed("vintage rows: kinds, 16:30 clocks, backfill rule, unchanged periods dropped, '.' kept")

# TEST: CHECKS
def test_checks():
    spec_dict = fred.FRED_SERIES_SPEC_DICT["DTB3"]
    row_pdf = fred.get_vintage_row_pdf(fred.get_period_pdf(fred.fetch_fred_page_list("DTB3", get_fake_getter(PERIOD_TUPLE_LIST))), "RUN1")
    issue_dict = fred.get_fred_issue_dict(row_pdf, spec_dict, "2026-01-01")
    assert not any(date_list for name_str, date_list in issue_dict.items() if name_str.startswith("blocking")), issue_dict
    assert issue_dict["reported_dot_value_first_row"] == ["2005-01-05"]
    assert sorted(issue_dict["reported_available_moved_to_next_business_day"]) == ["2005-01-06", "2005-01-07"]
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
    assert summary_dict["row_to_add_count_int"] == 9 and not os.path.exists(file_path_str)
    assert not os.path.exists(f"{config.INGEST_RECORD_PATH_STR}downloads")
    # APPLY WRITES THE FILE AND THE PAGES
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN1", get_fake_getter(PERIOD_TUPLE_LIST), "2026-01-01")
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert len(published_pdf) == 9 and list(published_pdf.columns) == fred.FRED_COLUMN_STR_LIST
    assert len(glob.glob(f"{config.INGEST_RECORD_PATH_STR}downloads/fred_dtb3/RUN1/page_*.json")) == 7
    first_bytes = open(file_path_str, "rb").read()
    # A RERUN ADDS NOTHING AND LEAVES THE FILE BYTE-FOR-BYTE
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN2", get_fake_getter(PERIOD_TUPLE_LIST), "2026-01-01")
    assert summary_dict["row_to_add_count_int"] == 0 and open(file_path_str, "rb").read() == first_bytes
    # A NEW VINTAGE ADDS ONE REVISION ROW; EARLIER ROWS KEEP THEIR RUN ID
    new_list = PERIOD_TUPLE_LIST + [("2005-02-01", "9999-12-31", "2005-01-04", "2.28")]
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN3", get_fake_getter(new_list), "2026-01-01")
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert summary_dict["row_to_add_count_int"] == 1 and len(published_pdf) == 10
    assert published_pdf["ingest_run_id"].value_counts().to_dict() == {"RUN1": 9, "RUN3": 1}
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

# TEST: A PARTIAL OR CHANGED ANSWER STOPS THE RUN; A WRITE THAT DOES NOT READ BACK IS UNDONE
def test_partial_and_readback():
    file_path_str = f"{config.RAWZONE_PATH_STR}fred_dtb3/dtb3.csv"
    # AN ANSWER WITHOUT ITS ROW COUNT, OR THAT ENDS BEFORE IT, IS REFUSED (ONE PAGE MUST NEVER PASS FOR THE WHOLE ANSWER)
    def no_count_getter(endpoint_str_in, query_dict_in):
        return json.dumps({"vintage_dates": ["2005-01-03"]}).encode("utf-8")
    def short_getter(endpoint_str_in, query_dict_in):
        return json.dumps({"count": 5, "vintage_dates": ["2005-01-03"] if query_dict_in["offset"] == 0 else []}).encode("utf-8")
    for getter in [no_count_getter, short_getter]:
        try:
            fred.fetch_fred_page_list("DTB3", getter)
            raise AssertionError("a partial answer was accepted")
        except fred.FredError as error:
            assert "partial" in str(error)
    # PUBLISHED ROWS THE SOURCE NO LONGER SENDS STOP THE RUN; THE FILE IS UNTOUCHED
    before_bytes = open(file_path_str, "rb").read()
    published_list = PERIOD_TUPLE_LIST + [("2005-02-01", "9999-12-31", "2005-01-04", "2.28")]   # WHAT test_publish LEFT PUBLISHED
    fewer_list = [period for period in published_list if period[2] != "2005-01-07"]
    summary_dict = fred.run_fred_series_dict("DTB3", True, "RUN5", get_fake_getter(fewer_list), "2026-01-01")
    assert summary_dict["published_unseen_count_int"] == 1 and "stopped_str" in summary_dict and open(file_path_str, "rb").read() == before_bytes
    # A WRITE THAT DOES NOT READ BACK AS WRITTEN: THE PREVIOUS FILE IS PUT BACK AND THE RUN STOPS
    published_pdf = fred.read_published_pdf(file_path_str)
    original_read = fred.pd.read_csv
    fred.pd.read_csv = lambda *args_in, **kwargs_in: original_read(*args_in, **kwargs_in).iloc[:-1]
    try:
        fred.write_union_verified(published_pdf, file_path_str, f"{TEST_ROOT_PATH_STR}readback_backup/", fred.FRED_COLUMN_STR_LIST, fred.FredError)
        raise AssertionError("a file that did not read back was accepted")
    except fred.FredError:
        pass
    finally:
        fred.pd.read_csv = original_read
    assert open(file_path_str, "rb").read() == before_bytes
    # A GOOD WRITE READS BACK AND LEAVES THE SAME BYTES
    fred.write_union_verified(published_pdf, file_path_str, f"{TEST_ROOT_PATH_STR}readback_backup2/", fred.FRED_COLUMN_STR_LIST, fred.FredError)
    assert open(file_path_str, "rb").read() == before_bytes
    passed("partial answers refused; published rows no longer sent stop the run; a write that does not read back is undone")

# TEST: ERRORS NEVER CARRY THE KEY
def test_key_never_shown():
    secret_str = "abcdefghij0123456789abcdefghij01"
    def failing_getter(endpoint_str_in, query_dict_in):
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
            fred.get_fred_response_bytes("series/observations", {"series_id": "DTB3"}, secret_str)
        except fred.FredError as error:
            assert secret_str not in str(error) and "HTTP 400" in str(error), str(error)
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(f"https://x?api_key={secret_str}")):
        try:
            fred.get_fred_response_bytes("series/observations", {"series_id": "DTB3"}, secret_str)
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

# TEST: A RUN THAT FAILS INSIDE ITS START (E.G. A CONSOLE THAT CANNOT PRINT) RELEASES ITS LOCK; --break-lock MARKS AN OLD RUN ABANDONED
def test_run_start_failure():
    import builtins
    from ingest.records import ScriptRun
    record_path_str = tempfile.mkdtemp(prefix="so_fred_records_").replace("\\", "/") + "/"
    real_print = builtins.print
    def failing_print(*args_in, **kwargs_in):
        if args_in and str(args_in[0]).startswith("ℹ️ Run "):
            raise UnicodeEncodeError("charmap", "ℹ", 0, 1, "character maps to <undefined>")
        return real_print(*args_in, **kwargs_in)
    builtins.print = failing_print
    try:
        with ScriptRun("test", {}, "fred_dtb3", record_path_str_in=record_path_str):
            raise AssertionError("body must not run")
    except UnicodeEncodeError:
        pass
    finally:
        builtins.print = real_print
    assert not os.path.exists(f"{record_path_str}locks/fred_dtb3.lock")
    manifest_list = glob.glob(f"{record_path_str}runs/*.json")
    assert len(manifest_list) == 1 and json.load(open(manifest_list[0], encoding="utf-8"))["status_str"] == "failed"
    # A LOCK LEFT BY A RUN THAT IS GONE: --break-lock REMOVES IT AND MARKS THAT RUN'S MANIFEST abandoned
    os.makedirs(f"{record_path_str}locks", exist_ok=True)
    with open(f"{record_path_str}runs/OLDRUN.json", "w", encoding="utf-8") as file:
        json.dump({"run_id_str": "OLDRUN", "status_str": "running"}, file)
    with open(f"{record_path_str}locks/fred_dtb3.lock", "w", encoding="utf-8") as file:
        json.dump({"run_id_str": "OLDRUN"}, file)
    with ScriptRun("test", {}, "fred_dtb3", break_lock_bool_in=True, record_path_str_in=record_path_str) as run:
        new_run_id_str = run.run_id_str
    old_dict = json.load(open(f"{record_path_str}runs/OLDRUN.json", encoding="utf-8"))
    assert old_dict["status_str"] == "abandoned" and old_dict["abandoned_by_run_id_str"] == new_run_id_str
    assert not os.path.exists(f"{record_path_str}locks/fred_dtb3.lock")
    # OUTPUT THROUGH A PIPE IN cp1252 STILL PRINTS (ingest RECONFIGURES THE STREAMS TO UTF-8)
    env_dict = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": REPO_PATH_STR, "SO_INGEST_TARGET_ROOT": TEST_ROOT_PATH_STR}
    result = subprocess.run([sys.executable, "-c", "import ingest; print('ℹ️ ✅ ❌')"], env=env_dict, capture_output=True)
    assert result.returncode == 0 and result.stdout.decode("utf-8").strip() == "ℹ️ ✅ ❌", result
    passed("run start failure releases the lock and records failed; --break-lock marks the old run abandoned; UTF-8 output through a cp1252 pipe")

# RUN THE TESTS
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_paging()
    test_vintage_rows()
    test_checks()
    test_publish()
    test_partial_and_readback()
    test_key_never_shown()
    test_script()
    test_run_start_failure()
    print("\nAll FRED tests passed ✅")
