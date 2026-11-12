import os
import re
import glob
import datetime
import tomllib

"""
Stock Overflow Ingest Configuration

Every constant of the ingest lives here: where the target data tree is, the IBKR connection, the contract, the historical
request settings, the pacing rules, and the staging / merge conventions.

Target root (the complete data tree, i.e. the quarantine; NEVER the development view):
    1. the environment variable SO_INGEST_TARGET_ROOT, if set;
    2. otherwise target_root in config/local.toml (git-ignored; template: config/local.example.toml);
    3. otherwise unset: every script stops at check_target_root_str() with a message. There is no built-in default, so no
       machine address or path is written in this repository.
    On the desktop the root is the local quarantine folder; on the laptop it is the network share that points to it.
    The ingest never reads or sets the research workspace's variables (SO_DATA_PATH, SO_QUARANTINE_PATH, SO_HOLDOUT_PATH).

Folder names (the interface with stock_overflow_workspace, docs/DATA_CONTRACT.md):
    <root>/store01_rawzone/ibkr_spy_1min/              SPY 1-minute raw files (the research reads every CSV here)
    <root>/store01_rawzone/ibkr_spy_1min_staging/      SPY downloads waiting to be merged, logs
    <root>/store01_rawzone/ibkr_vix_family[_staging]/  VIX and VIX3M (ingest.index_config)
    <root>/store01_rawzone/_ingest/                    ingest-only records: run manifests, locks (never read by the research)

Staging:
    Downloads never go straight into a raw folder. They are written to staging, checked, and only then added to the raw
    folder by the merge scripts (dry run by default).
"""

"""
Machine Settings (config/local.toml, git-ignored)
"""

# DEFINE THE REPOSITORY ROOT AND THE LOCAL SETTINGS FILE
REPO_ROOT_PATH_STR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace("\\", "/") + "/"
LOCAL_CONFIG_FILE_PATH_STR = f"{REPO_ROOT_PATH_STR}config/local.toml"
# DEFINE THE ENVIRONMENT VARIABLE OF THE TARGET ROOT
TARGET_ROOT_ENV_STR = "SO_INGEST_TARGET_ROOT"
# DEFINE THE TEXT USED WHEN NO TARGET ROOT IS CONFIGURED (NOT A VALID FOLDER NAME ON WINDOWS, SO NOTHING CAN BE WRITTEN THERE BY ACCIDENT)
UNSET_ROOT_PATH_STR = "<target root not set>/"

# FUNCTION: READ THE LOCAL SETTINGS FILE
def get_local_setting_dict(file_path_str_in=LOCAL_CONFIG_FILE_PATH_STR):
    """
    Args:
        file_path_str_in (str): Path of the local TOML file

    Returns:
        dict: The settings (empty when the file does not exist)
    """
    # IF THE FILE DOES NOT EXIST
    if not os.path.exists(file_path_str_in):
        return {}
    # READ THE FILE (utf-8-sig: A FILE SAVED BY WINDOWS POWERSHELL 5 STARTS WITH A BYTE ORDER MARK)
    with open(file_path_str_in, encoding="utf-8-sig") as file:
        return tomllib.loads(file.read())

# FUNCTION: NORMALIZE A FOLDER PATH
def get_folder_str(path_str_in):
    """
    Args:
        path_str_in (str): Folder path

    Returns:
        str: The path with forward slashes and one trailing "/"
    """
    # RETURN THE NORMALIZED PATH
    return path_str_in.replace("\\", "/").rstrip("/") + "/"

# COLLECT THE LOCAL SETTINGS
LOCAL_SETTING_DICT = get_local_setting_dict()
# DEFINE THE TARGET ROOT (ENVIRONMENT, THEN LOCAL FILE, THEN UNSET)
_TARGET_ROOT_RAW_STR = os.environ.get(TARGET_ROOT_ENV_STR) or LOCAL_SETTING_DICT.get("target_root", "")
TARGET_ROOT_PATH_STR = get_folder_str(_TARGET_ROOT_RAW_STR) if _TARGET_ROOT_RAW_STR else UNSET_ROOT_PATH_STR

"""
Data Folders
"""

# DEFINE THE RAW ZONE
RAWZONE_PATH_STR = f"{TARGET_ROOT_PATH_STR}store01_rawzone/"
# DEFINE THE SPY RAW AND STAGING FOLDER NAMES (THE RAW NAME IS THE RESEARCH'S so.paths.LOCAL_OHLCV_DATA_FILE_PATH_STR LEAF)
SPY_RAW_FOLDER_NAME_STR = "ibkr_spy_1min"
SPY_STAGING_FOLDER_NAME_STR = "ibkr_spy_1min_staging"
# DEFINE THE RAW IBKR MINUTE BAR FOLDER
RAW_OHLCV_PATH_STR = f"{RAWZONE_PATH_STR}{SPY_RAW_FOLDER_NAME_STR}/"
# DEFINE THE STAGING FOLDER (NEW DOWNLOADS WAIT HERE UNTIL THEY ARE MERGED INTO THE RAW FOLDER)
STAGING_OHLCV_PATH_STR = f"{RAWZONE_PATH_STR}{SPY_STAGING_FOLDER_NAME_STR}/"
# DEFINE THE FOLDER WHERE MERGED STAGING FILES ARE MOVED (KEPT AS A RECORD OF WHAT WAS ADDED AND WHEN)
STAGING_MERGED_PATH_STR = f"{STAGING_OHLCV_PATH_STR}merged/"
# DEFINE THE DOWNLOAD AND MERGE LOGS (ONLY EVER APPENDED TO; "_v2" BECAUSE THE COLUMNS CHANGED IN THIS REPOSITORY: THE OLD LOGS ARE KEPT AS THEY ARE)
DOWNLOAD_LOG_FILE_PATH_STR = f"{STAGING_OHLCV_PATH_STR}download_log_v2.csv"
MERGE_LOG_FILE_PATH_STR = f"{STAGING_OHLCV_PATH_STR}merge_log_v2.csv"
# DEFINE THE INGEST RECORDS FOLDER (RUN MANIFESTS AND LOCKS; NOT NAMED IN so.paths, SO NEVER COPIED INTO THE DEVELOPMENT VIEW)
INGEST_RECORD_PATH_STR = f"{RAWZONE_PATH_STR}_ingest/"
RUN_MANIFEST_PATH_STR = f"{INGEST_RECORD_PATH_STR}runs/"
LOCK_PATH_STR = f"{INGEST_RECORD_PATH_STR}locks/"
# DEFINE THE FOLDER OF THE JOURNALS OF MERGES IN PROGRESS (ingest.raw_files): A JOURNAL LEFT BEHIND MEANS A MERGE WAS INTERRUPTED,
# AND EVERY SCRIPT REFUSES TO RUN UNTIL scripts/ingest/restore_pending_merge.py HAS PUT THE FILES BACK
PENDING_PATH_STR = f"{INGEST_RECORD_PATH_STR}pending/"

# DEFINE THE DEFAULT PRINT CUT-OFF AND THE CUT-OFF USED WHEN THE SETTING IS NOT USABLE (NO DATE IS EVER SHOWN THEN)
PRINT_DATES_BEFORE_DEFAULT_STR = "2025-01-01"
PRINT_DATES_BEFORE_REFUSED_STR = "1900-01-01"

# FUNCTION: READ THE PRINT CUT-OFF (FAIL CLOSED)
def get_print_dates_before_tup(setting_dict_in):
    """
    The cut-off must be a real date written YYYY-MM-DD (it is compared as text, so any other form could let later dates through),
    and not later than PRINT_DATES_BEFORE_DEFAULT_STR unless print_dates_before_later_ok = true is also set.

    Args:
        setting_dict_in (dict): Local settings

    Returns:
        tuple: (cut-off "YYYY-MM-DD", problem text: "" when usable; when not usable the cut-off is PRINT_DATES_BEFORE_REFUSED_STR)
    """
    # READ THE VALUE (A TOML DATE WITHOUT QUOTES ARRIVES AS A date OBJECT)
    text_str = str(setting_dict_in.get("print_dates_before", PRINT_DATES_BEFORE_DEFAULT_STR)).strip()
    # IT MUST BE A REAL YYYY-MM-DD DATE
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text_str):
            raise ValueError
        datetime.date.fromisoformat(text_str)
    except ValueError:
        return PRINT_DATES_BEFORE_REFUSED_STR, "print_dates_before in config/local.toml is not a date written YYYY-MM-DD."
    # A LATER CUT-OFF NEEDS AN EXPLICIT SECOND SETTING
    if text_str > PRINT_DATES_BEFORE_DEFAULT_STR and setting_dict_in.get("print_dates_before_later_ok") is not True:
        return PRINT_DATES_BEFORE_REFUSED_STR, (f"print_dates_before in config/local.toml is later than {PRINT_DATES_BEFORE_DEFAULT_STR}. "
                                                "Set print_dates_before_later_ok = true as well, and only if it is still before the holdout window.")
    # RETURN THE USABLE CUT-OFF
    return text_str, ""

# DEFINE THE FIRST DATE THAT A PRINTED LINE OR A MANIFEST MAY NOT NAME (NON-BAR SOURCES PRINT COUNTS, AND DATES ONLY BEFORE IT).
# SET print_dates_before IN config/local.toml TO THE START OF THE ACTIVE HOLDOUT WINDOW OR EARLIER. AN UNUSABLE VALUE SHOWS NO DATE
# AND STOPS EVERY SCRIPT AT check_target_root_str().
PRINT_DATES_BEFORE_STR, PRINT_DATES_BEFORE_PROBLEM_STR = get_print_dates_before_tup(LOCAL_SETTING_DICT)

# DEFINE THE FILES THAT MARK A DEVELOPMENT VIEW (WRITTEN BY THE RESEARCH'S refresh_development_view.py IN THE SO_DATA_PATH ROOT)
VIEW_MARKER_FILE_NAME_LIST = ["VIEW.json", "view_manifest.json"]

# FUNCTION: CHECK THE TARGET ROOT
def get_target_root_problem_str(root_path_str_in=None):
    """
    Checks that the target root is set, exists, holds store01_rawzone/, and is not a development view.

    Args:
        root_path_str_in (str | None): Root to check (default: the configured one)

    Returns:
        str: "" when the root is usable, otherwise one paragraph (this never raises)
    """
    # DEFINE THE ROOT
    root_str = TARGET_ROOT_PATH_STR if root_path_str_in is None else get_folder_str(root_path_str_in)
    # THE ROOT MUST BE SET
    if root_str == UNSET_ROOT_PATH_STR:
        return (f"No target root is set. Set {TARGET_ROOT_ENV_STR} or target_root in config/local.toml (copy config/local.example.toml). "
                "It must be the complete data tree (the quarantine), never the development view.")
    # THE ROOT MUST EXIST AND HOLD THE RAW ZONE
    if not os.path.isdir(root_str + "store01_rawzone"):
        return f"The target root {root_str} has no store01_rawzone/ folder (is the share reachable, is the path right?)."
    # THE ROOT MUST NOT BE A DEVELOPMENT VIEW
    marker_str_list = [name_str for name_str in VIEW_MARKER_FILE_NAME_LIST if os.path.exists(root_str + name_str)]
    if marker_str_list:
        return f"The target root {root_str} holds {', '.join(marker_str_list)}: it is a development view. The ingest writes only to the quarantine."
    # THE ROOT MUST NOT BE THE RESEARCH DATA ROOT OF THIS PROCESS
    research_root_str = os.environ.get("SO_DATA_PATH", "")
    if research_root_str and os.path.normcase(os.path.abspath(research_root_str)) == os.path.normcase(os.path.abspath(root_str)):
        return f"The target root {root_str} is SO_DATA_PATH (the development view). The ingest writes only to the quarantine."
    # THE ROOT IS USABLE
    return ""

# FUNCTION: LIST THE JOURNALS OF INTERRUPTED MERGES
def get_pending_journal_list(pending_path_str_in=None):
    """
    Args:
        pending_path_str_in (str | None): Journal folder (default: PENDING_PATH_STR)

    Returns:
        list[str]: Journal files, oldest first (empty when none)
    """
    # LIST THE JSON FILES OF THE FOLDER
    return sorted(glob.glob(f"{pending_path_str_in or PENDING_PATH_STR}*.json"))

# FUNCTION: STOP A SCRIPT WHEN THE TARGET ROOT OR THE SETTINGS ARE NOT USABLE
def check_target_root_str(skip_bool_in=False, allow_pending_bool_in=False):
    """
    Args:
        skip_bool_in (bool): Skip the check (tests on temporary folders only)
        allow_pending_bool_in (bool): Do not stop on journals of interrupted merges (only the script that restores them)

    Returns:
        str: The target root

    Raises:
        SystemExit: (code 1) when the root is not usable, the print cut-off is not usable, or an interrupted merge is pending
    """
    # IF THE CHECK IS SKIPPED
    if skip_bool_in:
        return TARGET_ROOT_PATH_STR
    # CHECK THE ROOT, THEN THE PRINT CUT-OFF
    problem_str = get_target_root_problem_str() or PRINT_DATES_BEFORE_PROBLEM_STR
    # A MERGE INTERRUPTED BETWEEN MOVING ITS SOURCES AND WRITING ITS TARGET MUST BE RESTORED FIRST (RAW MAY LACK THOSE DAYS)
    if not problem_str and not allow_pending_bool_in and get_pending_journal_list():
        problem_str = (f"An interrupted merge left {len(get_pending_journal_list())} journal(s) in {PENDING_PATH_STR}: the raw folder may lack the rows "
                       "it was moving. Run scripts/ingest/restore_pending_merge.py (dry run, then --apply) before anything else.")
    # STOP WITH THE MESSAGE
    if problem_str:
        print(f"❌ {problem_str}")
        raise SystemExit(1)
    # RETURN THE ROOT
    return TARGET_ROOT_PATH_STR

# FUNCTION: CHECK A FOLDER THAT A SCRIPT WILL WRITE TO (OR CHANGE IN PLACE)
def get_write_path_problem_str(path_str_in, root_path_str_in=None):
    """
    A folder given on the command line (--raw, --staging, --source, --target) must lie inside <target root>/store01_rawzone/, must
    not be inside a development view (no VIEW.json or view_manifest.json in it or in any folder above it) and must not be inside
    SO_DATA_PATH.

    Args:
        path_str_in (str): Folder
        root_path_str_in (str | None): Target root (default: the configured one)

    Returns:
        str: "" when the folder may be written, otherwise the reason (this never raises)
    """
    # DEFINE THE ROOT
    root_str = TARGET_ROOT_PATH_STR if root_path_str_in is None else get_folder_str(root_path_str_in)
    if root_str == UNSET_ROOT_PATH_STR:
        return "No target root is set, so no folder may be written."
    # NORMALIZE BOTH PATHS (ABSOLUTE, CASE-FOLDED ON WINDOWS)
    rawzone_str = os.path.normcase(os.path.abspath(root_str + "store01_rawzone"))
    path_str = os.path.normcase(os.path.abspath(path_str_in))
    # THE FOLDER MUST BE INSIDE THE RAW ZONE OF THE TARGET ROOT (DIFFERENT DRIVES: OUTSIDE)
    try:
        inside_bool = os.path.commonpath([rawzone_str, path_str]) == rawzone_str
    except ValueError:
        inside_bool = False
    if not inside_bool:
        return f"The folder {path_str_in} is outside {root_str}store01_rawzone/. The ingest writes only inside the quarantine's raw zone."
    # NO DEVELOPMENT VIEW MARKER IN THE FOLDER OR ANY FOLDER ABOVE IT
    folder_str = path_str
    while True:
        for marker_str in VIEW_MARKER_FILE_NAME_LIST:
            if os.path.exists(os.path.join(folder_str, marker_str)):
                return f"The folder {path_str_in} is inside a development view ({marker_str} in {folder_str}). The ingest writes only to the quarantine."
        parent_str = os.path.dirname(folder_str)
        if parent_str == folder_str:
            break
        folder_str = parent_str
    # NOT INSIDE THE RESEARCH DATA ROOT OF THIS PROCESS
    research_root_str = os.environ.get("SO_DATA_PATH", "").strip()
    if research_root_str:
        research_path_str = os.path.normcase(os.path.abspath(research_root_str))
        try:
            under_bool = os.path.commonpath([research_path_str, path_str]) == research_path_str
        except ValueError:
            under_bool = False
        if under_bool:
            return f"The folder {path_str_in} is inside SO_DATA_PATH (the development view). The ingest writes only to the quarantine."
    # THE FOLDER MAY BE WRITTEN
    return ""

# FUNCTION: STOP A SCRIPT WHEN A FOLDER IT WILL WRITE IS NOT USABLE
def check_write_path_str(path_str_in, skip_bool_in=False):
    """
    Args:
        path_str_in (str): Folder from the command line
        skip_bool_in (bool): Skip the check (tests on temporary folders only, with --skip-root-check)

    Returns:
        str: The folder with forward slashes and one trailing "/"

    Raises:
        SystemExit: (code 1) when the folder may not be written
    """
    # IF THE CHECK IS NOT SKIPPED, STOP ON A PROBLEM
    problem_str = "" if skip_bool_in else get_write_path_problem_str(path_str_in)
    if problem_str:
        print(f"❌ {problem_str}")
        raise SystemExit(1)
    # RETURN THE NORMALIZED FOLDER
    return get_folder_str(path_str_in)

"""
File Conventions (identical to the existing raw files)
"""

# DEFINE THE FILE NAME PREFIX (ohlcv_data_YYYY.csv, ohlcv_data_YYYYMM.csv, ohlcv_data_YYYYMMDD.csv)
OHLCV_FILE_PREFIX_STR = "ohlcv_data"
# DEFINE THE COLUMNS OF A DOWNLOADED SESSION FILE (created_ts IS LEFT EMPTY, AS IN EVERY EXISTING RAW FILE)
OHLCV_COL_STR_LIST = ["timestamp", "open", "high", "low", "close", "volume", "created_ts", "date"]
# DEFINE THE TIMESTAMP TEXT FORMAT (NEW YORK TIME WITH OFFSET, E.G. 2026-07-17 09:30:00-04:00)
TIMESTAMP_FORMAT_STR = "%Y-%m-%d %H:%M:%S%z"
# DEFINE THE NEW YORK TIMEZONE NAME
NY_TZ_STR = "America/New_York"

"""
IBKR Connection (IB Gateway live: 4001, IB Gateway paper: 4002, TWS live: 7496, TWS paper: 7497)
Turn on "Read-Only API" in the Gateway's API settings: the ingest never places orders and never requests account data.
"""

# DEFINE THE HOST AND PORT (LOCAL SETTINGS MAY OVERRIDE THEM)
IBKR_HOST_STR = str(LOCAL_SETTING_DICT.get("ibkr_host", "127.0.0.1"))
IBKR_PORT_INT = int(LOCAL_SETTING_DICT.get("ibkr_port", 4001))
# DEFINE THE CLIENT ID OF THE DOWNLOADER
IBKR_DOWNLOAD_CLIENT_ID_INT = int(LOCAL_SETTING_DICT.get("ibkr_client_id", 1))
# DEFINE THE MAXIMUM WAIT FOR THE CONNECTION HANDSHAKE (nextValidId) (seconds)
IBKR_CONNECT_TIMEOUT_SECONDS = 15

"""
Contract (SPY on SMART routing, primary exchange ARCA: the contract of every existing raw file)
"""

# DEFINE THE CONTRACT FIELDS
CONTRACT_SYMBOL_STR = "SPY"
CONTRACT_SEC_TYPE_STR = "STK"
CONTRACT_CURRENCY_STR = "USD"
CONTRACT_EXCHANGE_STR = "SMART"
CONTRACT_PRIMARY_EXCHANGE_STR = "ARCA"

"""
Historical Request Settings (one request per session: 1-minute TRADES bars, regular trading hours)
"""

# DEFINE THE BAR SIZE, DATA TYPE, REGULAR TRADING HOURS FLAG AND DATE FORMAT (2 = EPOCH SECONDS)
BAR_SIZE_STR = "1 min"
WHAT_TO_SHOW_STR = "TRADES"
USE_RTH_INT = 1
FORMAT_DATE_INT = 2
# DEFINE THE TIMEZONE SUFFIX OF THE END DATETIME ("US/Eastern" IS THE NAME THE EXISTING NOTEBOOK USED SUCCESSFULLY)
IBKR_END_DATETIME_TZ_STR = "US/Eastern"
# DEFINE THE MAXIMUM WAIT FOR ONE HISTORICAL REQUEST (seconds; the old notebook used 10, too short for old sessions)
REQUEST_TIMEOUT_SECONDS = 60
# DEFINE THE NUMBER OF ATTEMPTS PER SESSION (A PARTIAL OR FAILED SESSION IS REQUESTED AGAIN UP TO THIS COUNT)
REQUEST_ATTEMPT_COUNT = 3
# DEFINE THE DELAY AFTER THE SESSION CLOSE BEFORE A SESSION IS CONSIDERED DOWNLOADABLE (minutes)
SESSION_CLOSE_BUFFER_MINUTES = 5

"""
Pacing (IBKR historical data limits)

    - at most 50 simultaneous open historical requests;
    - no identical request within 15 seconds;
    - no 6 or more requests for the same contract, exchange and data type within 2 seconds;
    - (60 requests per 10 minutes applies to bars of 30 seconds or less, not to 1-minute bars).
The defaults stay well inside these limits: 4 requests in flight and at least 0.5 s between two submissions
(at most 4 requests per 2 seconds).
"""

# DEFINE THE MAXIMUM NUMBER OF REQUESTS IN FLIGHT
MAX_IN_FLIGHT_REQUEST_COUNT = 4
# DEFINE THE MINIMUM INTERVAL BETWEEN TWO REQUEST SUBMISSIONS (seconds)
REQUEST_MIN_INTERVAL_SECONDS = 0.5
# DEFINE THE WAIT AFTER A PACING VIOLATION BEFORE THE NEXT SUBMISSION (seconds; doubled after each violation, capped)
PACING_BACKOFF_SECONDS = 15
PACING_BACKOFF_MAX_SECONDS = 300

"""
IBKR Message Codes
"""

# DEFINE THE INFORMATIONAL CODE RANGE (DATA FARM STATUS MESSAGES, NOT ERRORS)
IBKR_INFO_CODE_MIN_INT = 2100
IBKR_INFO_CODE_MAX_INT = 2199
# DEFINE THE CONNECTIVITY CODES (1100 LOST, 1101 RESTORED DATA LOST, 1102 RESTORED DATA KEPT)
IBKR_CONNECTIVITY_CODE_LIST = [1100, 1101, 1102]
# DEFINE THE HISTORICAL DATA SERVICE ERROR CODE (PACING VIOLATIONS AND "NO DATA" BOTH ARRIVE WITH THIS CODE)
IBKR_HMDS_ERROR_CODE_INT = 162
# DEFINE THE TEXT THAT IDENTIFIES A PACING VIOLATION AND AN EMPTY ANSWER INSIDE A 162 MESSAGE (lower case)
IBKR_PACING_TEXT_STR = "pacing violation"
IBKR_NO_DATA_TEXT_STR = "returned no data"
