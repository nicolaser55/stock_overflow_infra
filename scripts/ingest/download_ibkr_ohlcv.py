import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
# IMPORT THE IBKR CLIENT
from ingest.ibkr_client import IbkrApp
# IMPORT THE DOWNLOAD FUNCTIONS
from ingest.ibkr_download import get_download_session_pdf, download_session_pdf

"""
Download SPY 1-minute bars from IBKR into the staging folder (store01_rawzone/ibkr_spy_1min_staging/)

Prerequisite: IB Gateway (or TWS) running and logged in, API enabled on the port below.

    python scripts/ingest/download_ibkr_ohlcv.py                                   # every closed session after the last one present
    python scripts/ingest/download_ibkr_ohlcv.py --start 2026-08-14 --end 2026-10-05
    python scripts/ingest/download_ibkr_ohlcv.py --dates 2009-07-27 2013-12-23 --redownload
    python scripts/ingest/download_ibkr_ohlcv.py --start 2026-08-14 --list         # only list the sessions (no connection)

Then check and merge the staged files into the raw folder: python scripts/ingest/merge_staging_into_raw.py
"""

# FUNCTION: PARSE THE COMMAND LINE ARGUMENTS
def get_args():
    """
    Returns:
        argparse.Namespace: Command line arguments
    """
    # CREATE THE PARSER
    parser = argparse.ArgumentParser(description="Download IBKR 1-minute bars into the staging folder.")
    parser.add_argument("--start", help="first date (default: the day after the last date in raw + staging)")
    parser.add_argument("--end", help="last date (default: today; open sessions are skipped)")
    parser.add_argument("--dates", nargs="*", help="explicit dates instead of a range")
    parser.add_argument("--redownload", action="store_true", help="download even the sessions already present (staging file replaced)")
    parser.add_argument("--list", action="store_true", help="list the sessions to download and stop")
    parser.add_argument("--host", default=config.IBKR_HOST_STR)
    parser.add_argument("--port", type=int, default=config.IBKR_PORT_INT, help="4001 Gateway live, 4002 Gateway paper, 7496 / 7497 TWS")
    parser.add_argument("--client-id", type=int, default=config.IBKR_DOWNLOAD_CLIENT_ID_INT)
    parser.add_argument("--max-in-flight", type=int, default=config.MAX_IN_FLIGHT_REQUEST_COUNT)
    parser.add_argument("--staging", default=config.STAGING_OHLCV_PATH_STR, help="staging folder")
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    # RETURN THE ARGUMENTS
    return parser.parse_args()

# FUNCTION: MAIN
def main():
    """
    Selects the sessions, connects, downloads, disconnects.
    """
    # PARSE THE ARGUMENTS
    args = get_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # DEFINE THE STAGING FOLDER (ENDING WITH "/")
    staging_path_str = args.staging.replace("\\", "/").rstrip("/") + "/"
    # SELECT THE SESSIONS
    session_pdf = get_download_session_pdf(args.start, args.end, args.dates, args.redownload, config.RAW_OHLCV_PATH_STR, staging_path_str)
    # DISPLAY INFORMATION
    print(f"Sessions to download: {len(session_pdf):,}" + (f" ({session_pdf['date'].min()} -> {session_pdf['date'].max()})" if len(session_pdf) else ""))
    # IF THERE IS NOTHING TO DO OR ONLY A LIST WAS ASKED FOR
    if session_pdf.empty or args.list:
        # PRINT THE DATES AND EXIT
        print([str(date_object) for date_object in session_pdf["date"]]) if args.list else None
        return
    # RECORD THE RUN (MANIFEST) AND HOLD THE SPY_1MIN LOCK WHILE STAGING FILES ARE WRITTEN
    with ScriptRun("download_ibkr_ohlcv", vars(args), "spy_1min", break_lock_bool_in=args.break_lock) as run:
        # CONNECT
        app = IbkrApp()
        if not app.connect_app(args.host, args.port, args.client_id):
            raise SystemExit(1)
        # DOWNLOAD, THEN ALWAYS DISCONNECT
        try:
            summary_pdf = download_session_pdf(app, session_pdf, staging_path_str_in=staging_path_str,
                                               log_file_path_str_in=f"{staging_path_str}download_log_v2.csv",
                                               max_in_flight_int_in=args.max_in_flight, run_id_str_in=run.run_id_str)
        finally:
            app.disconnect_app()
        # RECORD THE COUNTS IN THE MANIFEST
        run.summary_dict["status_count_dict"] = summary_pdf["status_str"].value_counts().to_dict() if not summary_pdf.empty else {}

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
