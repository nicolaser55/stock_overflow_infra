import os
import sys
import json
import glob
import tempfile
import subprocess

# POINT THE INGEST TO A TEMPORARY TARGET ROOT BEFORE IT IS IMPORTED (NEVER A REAL DATA FOLDER)
TEST_ROOT_PATH_STR = tempfile.mkdtemp(prefix="so_div_test_").replace("\\", "/") + "/"
os.makedirs(f"{TEST_ROOT_PATH_STR}store01_rawzone", exist_ok=True)
os.environ["SO_INGEST_TARGET_ROOT"] = TEST_ROOT_PATH_STR
REPO_PATH_STR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_PATH_STR)
import pandas as pd
import openpyxl
from ingest import config, dividends

"""
SPY Dividends (synthetic State Street workbook, Yahoo cross-check CSV and raw SPY bars written by this test; no network)

    python tests/test_dividends.py

The 2005 rows have State Street's published layout; every other amount is invented.
"""

# FUNCTION: PRINT A PASSED TEST
def passed(name_str_in):
    print(f"✅ {name_str_in}")

# DEFINE THE SYNTHETIC DISTRIBUTIONS (ex, record, pay as MM/DD/YYYY; amount text)
SPY_ROW_LIST = [
    ("12/17/2004", "12/21/2004", "01/31/2005", "0.400000"), ("12/31/2004", "01/04/2005", "01/31/2005", "0.100000"),   # 2004 (5 rows there): ignored
    ("03/18/2005", "03/22/2005", "04/29/2005", "0.467090"), ("06/17/2005", "06/21/2005", "07/29/2005", "0.487620"),
    ("09/16/2005", "09/20/2005", "10/31/2005", "0.521690"), ("12/16/2005", "12/20/2005", "01/31/2006", "0.671670"),
    ("03/17/2006", "03/21/2006", "04/28/2006", "0.500000"), ("06/16/2006", "06/20/2006", "07/31/2006", "0.510000"),
    ("09/15/2006", "09/19/2006", "10/31/2006", "0.520000"), ("12/15/2006", "12/19/2006", "01/31/2007", "0.530000"),
    ("03/16/2007", "03/20/2007", "04/30/2007", "0.540000"),                                                           # after "today": not used yet
]
TODAY_STR = "2006-12-31"
NOW_TS = pd.Timestamp("2006-12-31 12:00:00", tz="America/New_York")

# FUNCTION: WRITE A STATE STREET WORKBOOK
def write_ssga_file(row_list_in, file_path_str_in):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "dividend"
    sheet.append(["FUND NAME", "TICKER", "CUSIP", "EX-DATE", "RECORD DATE", "PAYABLE DATE", "DIVIDEND ($)", "SHORT TERM CAPITAL GAIN ($)", "LONG TERM CAPITAL GAIN ($)", "FREQUENCY"])
    sheet.append(["Other ETF", "XLF", "81369Y605", "03/18/2005", "03/22/2005", "04/29/2005", "0.100000", "", "", "Quarterly"])
    for ex_str, record_str, pay_str, amount_str in row_list_in:
        sheet.append(["State Street® SPDR® S&P 500® ETF Trust", "SPY", "78462F103", ex_str, record_str, pay_str, amount_str, "", "", "Quarterly"])
    workbook.save(file_path_str_in)

# FUNCTION: BUILD A YAHOO CROSS-CHECK CSV (FLOAT TEXT AS yfinance GIVES IT)
def get_yahoo_bytes(row_list_in, amount_override_dict_in=None, drop_set_in=()):
    amount_override_dict = amount_override_dict_in or {}
    line_list = ["ex_date,amount_usd"]
    for ex_str, _, _, amount_str in row_list_in:
        if ex_str in drop_set_in:
            continue
        iso_str = f"{ex_str[6:]}-{ex_str[:2]}-{ex_str[3:5]}"
        line_list.append(f"{iso_str},{amount_override_dict.get(ex_str, repr(float(amount_str)))}")
    return ("\n".join(line_list) + "\n").encode("utf-8")

# FUNCTION: WRITE A RAW SPY DAY FILE WITH TWO BARS (THE LAST CLOSE IS close)
def write_raw_day(date_str_in, close_float_in):
    os.makedirs(config.RAW_OHLCV_PATH_STR, exist_ok=True)
    rows = [f"{date_str_in} 15:58:00-05:00,1,1,1,{close_float_in - 1},10,,{date_str_in}", f"{date_str_in} 15:59:00-05:00,1,1,1,{close_float_in},10,,{date_str_in}"]
    with open(f"{config.RAW_OHLCV_PATH_STR}ohlcv_data_{date_str_in.replace('-', '')}.csv", "w") as file:
        file.write("timestamp,open,high,low,close,volume,created_ts,date\n" + "\n".join(rows) + "\n")

# PREPARE THE INPUTS
SSGA_PATH_STR = f"{TEST_ROOT_PATH_STR}ssga.xlsx"
write_ssga_file(SPY_ROW_LIST, SSGA_PATH_STR)
for previous_str in ["2005-03-17", "2005-06-16", "2005-09-15", "2005-12-15", "2006-03-16", "2006-06-15", "2006-09-14", "2006-12-14"]:
    write_raw_day(previous_str, 120.0)

# FUNCTION: RUN WITH THE TEST CLOCK
def run(apply_bool_in, run_id_str_in, crosscheck_bytes_in=None, ssga_path_str_in=SSGA_PATH_STR, now_ts_in=NOW_TS):
    crosscheck_bytes = crosscheck_bytes_in or get_yahoo_bytes(SPY_ROW_LIST)
    return dividends.run_dividend_dict(ssga_path_str_in, apply_bool_in, run_id_str_in, crosscheck_getter_in=lambda: crosscheck_bytes, today_str_in=TODAY_STR, now_ny_ts_in=now_ts_in)

# TEST: READING THE WORKBOOK AND THE CROSS-CHECK CSV
def test_read():
    ssga_pdf = dividends.read_ssga_pdf(SSGA_PATH_STR)
    assert len(ssga_pdf) == len(SPY_ROW_LIST)
    first_dict = ssga_pdf[ssga_pdf["ex_date"] == "2005-03-18"].iloc[0].to_dict()
    assert first_dict == {"ex_date": "2005-03-18", "record_date": "2005-03-22", "pay_date": "2005-04-29", "amount_usd": "0.467090"}, first_dict
    crosscheck_pdf = dividends.read_crosscheck_pdf(get_yahoo_bytes(SPY_ROW_LIST))
    assert crosscheck_pdf.loc[crosscheck_pdf["ex_date"] == "2005-03-18", "amount_usd"].iloc[0] == "0.46709"
    try:
        dividends.read_crosscheck_pdf(b"date,value\n2005-03-18,1\n")
        raise AssertionError("wrong columns accepted")
    except dividends.DividendError:
        pass
    passed("State Street workbook (SPY rows, ISO dates, amount text kept) and the Yahoo cross-check CSV read")

# TEST: THE yfinance READER (A FAKE yfinance: EX-DATES AT MIDNIGHT NEW YORK, FLOAT AMOUNTS)
def test_yahoo_reader():
    import types
    from unittest import mock
    index = pd.DatetimeIndex(["2005-03-18", "2005-06-17"]).tz_localize("America/New_York")
    fake_module = types.SimpleNamespace(Ticker=lambda symbol_str: types.SimpleNamespace(dividends=pd.Series([0.46709, 0.48762], index=index)))
    with mock.patch.dict(sys.modules, {"yfinance": fake_module}):
        crosscheck_pdf = dividends.read_crosscheck_pdf(dividends.get_yahoo_bytes())
    assert crosscheck_pdf.to_dict("records") == [{"ex_date": "2005-03-18", "amount_usd": "0.46709"}, {"ex_date": "2005-06-17", "amount_usd": "0.48762"}]
    empty_module = types.SimpleNamespace(Ticker=lambda symbol_str: types.SimpleNamespace(dividends=pd.Series([], dtype=float)))
    with mock.patch.dict(sys.modules, {"yfinance": empty_module}):
        try:
            dividends.get_yahoo_bytes()
            raise AssertionError("empty answer accepted")
        except dividends.DividendError:
            pass
    passed("Yahoo reader: New York ex-dates, amounts as sent; an empty answer stops")

# TEST: A CLEAN DRY RUN
def test_dry_run():
    summary_dict = run(False, "RUN1")
    assert "stopped_str" not in summary_dict, summary_dict
    assert summary_dict["used_row_count_int"] == 8 and summary_dict["row_to_add_count_int"] == 8 and summary_dict["matched_row_count_int"] == 8
    assert summary_dict["reported_no_previous_close"]["count_int"] == 0
    assert not os.path.exists(f"{config.RAWZONE_PATH_STR}spy_dividends")
    # THE SUMMARY HOLDS NO AMOUNT
    summary_str = json.dumps(summary_dict)
    assert "0.467090" not in summary_str and "0.46709" not in summary_str
    passed("dry run: 2005-2006 rows used (2004 and future ex-dates left out), cross-check matched, nothing written, no amount shown")

# TEST: BLOCKING CHECKS AND A RESOLUTION
def test_checks():
    # YAHOO DISAGREES ON ONE AMOUNT -> STOPPED
    disagree_bytes = get_yahoo_bytes(SPY_ROW_LIST, {"06/17/2005": "0.4877"})
    summary_dict = run(False, "RUN1", disagree_bytes)
    assert summary_dict["blocking_cross_source_disagreement"]["count_int"] == 1 and "stopped_str" in summary_dict
    # A RESOLUTION (FROM THE ANNUAL REPORT) SETTLES IT
    resolution_path_str = f"{config.INGEST_RECORD_PATH_STR}resolutions/{dividends.RESOLUTION_FILE_NAME_STR}"
    os.makedirs(os.path.dirname(resolution_path_str), exist_ok=True)
    pd.DataFrame([{"ex_date": "2005-06-17", "field": "amount_usd", "accepted_value": "0.487620", "note": "annual report 2005"}]).to_csv(resolution_path_str, index=False)
    summary_dict = run(False, "RUN1", disagree_bytes)
    assert "stopped_str" not in summary_dict and summary_dict["resolution_count_int"] == 1, summary_dict
    # AN EX-DATE YAHOO DOES NOT LIST -> STOPPED; A not_in_crosscheck RESOLUTION ACCEPTS IT
    missing_bytes = get_yahoo_bytes(SPY_ROW_LIST, drop_set_in={"09/16/2005"})
    pd.DataFrame(columns=dividends.RESOLUTION_COLUMN_STR_LIST).to_csv(resolution_path_str, index=False)
    summary_dict = run(False, "RUN1", missing_bytes)
    assert summary_dict["blocking_not_in_crosscheck"]["count_int"] == 1 and "stopped_str" in summary_dict
    pd.DataFrame([{"ex_date": "2005-09-16", "field": "not_in_crosscheck", "accepted_value": "accept", "note": "annual report 2005"}]).to_csv(resolution_path_str, index=False)
    summary_dict = run(False, "RUN1", missing_bytes)
    assert "stopped_str" not in summary_dict, summary_dict
    os.remove(resolution_path_str)
    # A MISSING DISTRIBUTION IN A CLOSED YEAR (2005) -> STOPPED; THE CURRENT YEAR (2006 ON THE TEST CLOCK) MAY HAVE FEWER
    short_path_str = f"{TEST_ROOT_PATH_STR}short.xlsx"
    write_ssga_file([row for row in SPY_ROW_LIST if row[0] != "09/16/2005"], short_path_str)
    summary_dict = run(False, "RUN1", ssga_path_str_in=short_path_str)
    assert summary_dict["blocking_year_not_four_distributions"]["count_int"] == 3, summary_dict
    # AN EX-DATE THAT IS NOT A SESSION, A PAY DATE BEFORE THE EX-DATE, AN AMOUNT ABOVE 3% OF THE CLOSE -> STOPPED
    bad_list = [("03/19/2005" if row[0] == "03/18/2005" else row[0], row[1], "06/01/2005" if row[0] == "06/17/2005" else row[2],
                 "5.000000" if row[0] == "09/16/2005" else row[3]) for row in SPY_ROW_LIST]
    bad_path_str = f"{TEST_ROOT_PATH_STR}bad.xlsx"
    write_ssga_file(bad_list, bad_path_str)
    summary_dict = run(False, "RUN1", get_yahoo_bytes(bad_list), ssga_path_str_in=bad_path_str)
    assert summary_dict["blocking_ex_date_not_session"]["count_int"] == 1
    assert summary_dict["blocking_pay_not_after_ex"]["count_int"] == 1
    assert summary_dict["blocking_amount_over_3pct_of_close"]["count_int"] == 1
    assert "5.000000" not in json.dumps(summary_dict)
    # THE SUMMARY NEVER NAMES A DATE ON OR AFTER THE CUT-OFF
    shown_dict = dividends.get_summary_dict({"blocking_x": ["2004-06-01", "2025-09-19"]}, {}, "2025-01-01")
    assert shown_dict["blocking_x"] == {"count_int": 2, "dates_before_2025-01-01": ["2004-06-01"]}
    passed("checks: disagreement and missing ex-date stop, resolutions settle them, year count, session, pay date, 3% of close; recent dates never shown")

# TEST: PUBLISH, RERUN, REVISION
def test_publish():
    file_path_str = f"{config.RAWZONE_PATH_STR}spy_dividends/spy_dividends.csv"
    summary_dict = run(True, "RUN1")
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert len(published_pdf) == 8 and list(published_pdf.columns) == dividends.DIVIDEND_COLUMN_STR_LIST
    first_dict = published_pdf.iloc[0].to_dict()
    assert first_dict == {"date": "2005-03-18", "available_ts": "2005-03-18 09:30:00-05:00", "ex_date": "2005-03-18", "record_date": "2005-03-22",
                          "pay_date": "2005-04-29", "declared_date": "", "amount_usd": "0.467090", "distribution_type": "regular", "currency": "USD",
                          "vintage_kind": "first_seen", "ingest_run_id": "RUN1"}, first_dict
    assert published_pdf.loc[published_pdf["ex_date"] == "2005-06-17", "available_ts"].iloc[0] == "2005-06-17 09:30:00-04:00"
    assert len(glob.glob(f"{config.INGEST_RECORD_PATH_STR}downloads/spy_dividends/RUN1/*")) == 2
    first_bytes = open(file_path_str, "rb").read()
    # RERUN: NOTHING ADDED, FILE BYTE-IDENTICAL
    summary_dict = run(True, "RUN2")
    assert summary_dict["row_to_add_count_int"] == 0 and open(file_path_str, "rb").read() == first_bytes
    # STATE STREET LATER CHANGES A PUBLISHED AMOUNT -> ONE REVISION ROW AT THE RETRIEVAL TIME; THE FIRST ROW STAYS
    revised_list = [(row[0], row[1], row[2], "0.468000" if row[0] == "03/18/2005" else row[3]) for row in SPY_ROW_LIST]
    revised_path_str = f"{TEST_ROOT_PATH_STR}revised.xlsx"
    write_ssga_file(revised_list, revised_path_str)
    later_ts = pd.Timestamp("2007-01-05 10:15:30", tz="America/New_York")
    summary_dict = run(True, "RUN3", get_yahoo_bytes(revised_list), ssga_path_str_in=revised_path_str, now_ts_in=later_ts)
    published_pdf = pd.read_csv(file_path_str, dtype=str, keep_default_na=False)
    assert summary_dict["revision_count_int"] == 1 and len(published_pdf) == 9
    revision_dict = published_pdf[published_pdf["vintage_kind"] == "revision"].iloc[0].to_dict()
    assert (revision_dict["ex_date"], revision_dict["date"], revision_dict["available_ts"], revision_dict["amount_usd"]) == ("2005-03-18", "2007-01-05", "2007-01-05 10:15:30-05:00", "0.468000")
    assert published_pdf.loc[(published_pdf["ex_date"] == "2005-03-18") & (published_pdf["vintage_kind"] == "first_seen"), "amount_usd"].iloc[0] == "0.467090"
    assert glob.glob(f"{config.RAWZONE_PATH_STR}spy_dividends_backup_*/spy_dividends.csv")
    passed("publish: ex-date 09:30 clock (EST/EDT), inputs kept, rerun byte-identical, later change published as a revision row")

# TEST: THE SCRIPT (DRY RUN WITH A SAVED CROSS-CHECK FILE; MISSING WORKBOOK COLUMNS STOP CLEANLY)
def test_script():
    yahoo_path_str = f"{TEST_ROOT_PATH_STR}yahoo.csv"
    with open(yahoo_path_str, "wb") as file:
        file.write(get_yahoo_bytes(SPY_ROW_LIST))
    env_dict = {**os.environ, "SO_INGEST_TARGET_ROOT": TEST_ROOT_PATH_STR, "PYTHONPATH": REPO_PATH_STR}
    # THE SCRIPT USES THE REAL CLOCK: A WORKBOOK OF CLOSED YEARS ONLY (2007 WOULD BE AN INCOMPLETE CLOSED YEAR)
    closed_path_str = f"{TEST_ROOT_PATH_STR}closed.xlsx"
    write_ssga_file([row for row in SPY_ROW_LIST if not row[0].endswith("2007")], closed_path_str)
    command_list = [sys.executable, f"{REPO_PATH_STR}/scripts/ingest/import_spy_dividends.py", "--ssga-file", closed_path_str, "--yahoo-file", yahoo_path_str]
    result = subprocess.run(command_list, env=env_dict, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0 and "would be added" in result.stdout, result.stdout + result.stderr
    broken_path_str = f"{TEST_ROOT_PATH_STR}broken.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["TICKER", "EX-DATE"])
    workbook.save(broken_path_str)
    result = subprocess.run([sys.executable, command_list[1], "--ssga-file", broken_path_str, "--yahoo-file", yahoo_path_str], env=env_dict, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 1 and "missing columns" in result.stdout, result.stdout + result.stderr
    passed("script: dry run with a saved cross-check file; a workbook with missing columns stops cleanly")

# RUN THE TESTS
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_read()
    test_yahoo_reader()
    test_dry_run()
    test_checks()
    test_publish()
    test_script()
    print("\nAll dividend tests passed ✅")
