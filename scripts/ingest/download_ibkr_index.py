import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
from ingest import index_config
# IMPORT THE INDEX DOWNLOAD
from ingest.index_download import IndexApp, get_index_task_list, download_index_task_pdf

"""
Download VIX / VIX3M daily and 1-minute bars from IBKR into the staging folders (nothing is added to the raw folders)

Prerequisite: IB Gateway (or TWS) running and logged in, API enabled on the port below.

    python scripts/ingest/download_ibkr_index.py --list                     # what would be downloaded (no connection)
    python scripts/ingest/download_ibkr_index.py --dates 2024-08-05 2025-04-09 --symbols VIX    # a small first trial
    python scripts/ingest/download_ibkr_index.py                           # VIX and VIX3M, daily and 1-minute, everything missing since the last date present
    python scripts/ingest/download_ibkr_index.py --from-start              # everything missing since the first date IBKR has (fills gaps anywhere)
    python scripts/ingest/download_ibkr_index.py --symbols VIX3M --bars daily

Folders: <data root>/store01_rawzone/ibkr_vix_family_staging/<index>_<bars>/ (staging), ibkr_vix_family/ (raw, see merge_index_staging_into_raw.py).
Interrupted or failed runs are safe to repeat: finished requests are saved, and already staged or raw dates are skipped.
Speed: about 2 requests per second at most; the full 1-minute history is about 5,000 sessions for VIX (about 20 years) and 4,200 for VIX3M.
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments, plans the requests, downloads them into the staging folders.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Download VIX / VIX3M bars from IBKR into the staging folders.")
    parser.add_argument("--symbols", nargs="*", default=list(index_config.INDEX_SPEC_DICT), choices=list(index_config.INDEX_SPEC_DICT))
    parser.add_argument("--bars", nargs="*", default=list(index_config.INDEX_BAR_KIND_DICT), choices=list(index_config.INDEX_BAR_KIND_DICT), help="1min and / or daily")
    parser.add_argument("--date1", help="first date (default: the day after the last date present, or the first date IBKR has)")
    parser.add_argument("--date2", help="last date (default: today)")
    parser.add_argument("--dates", nargs="*", help="explicit dates (replaces the range)")
    parser.add_argument("--from-start", action="store_true", help="start at the first date IBKR has, so that gaps anywhere are filled")
    parser.add_argument("--redownload", action="store_true", help="request dates already present too (1-minute: the staging file is replaced)")
    parser.add_argument("--list", action="store_true", help="list the plan and stop (no connection)")
    parser.add_argument("--max-in-flight", type=int, default=config.MAX_IN_FLIGHT_REQUEST_COUNT)
    parser.add_argument("--host", default=config.IBKR_HOST_STR)
    parser.add_argument("--port", type=int, default=config.IBKR_PORT_INT, help="4001 Gateway live, 4002 Gateway paper, 7496 / 7497 TWS")
    parser.add_argument("--client-id", type=int, default=config.IBKR_DOWNLOAD_CLIENT_ID_INT)
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # PLAN THE TASKS
    task_dict_list = []
    for symbol_str in args.symbols:
        for bar_kind_str in args.bars:
            task_dict_list += get_index_task_list(symbol_str, bar_kind_str, args.date1, args.date2, args.dates, args.from_start, args.redownload)
    # DISPLAY THE PLAN
    print(f"\nPlan: {len(task_dict_list):,} request(s)")
    for symbol_str in args.symbols:
        for bar_kind_str in args.bars:
            count_int = sum(1 for task_dict in task_dict_list if task_dict["symbol_str"] == symbol_str and task_dict["bar_kind_str"] == bar_kind_str)
            print(f"  {symbol_str:<6} {bar_kind_str:<6} {count_int:>6,}  -> {index_config.get_index_folder_path_str(symbol_str, bar_kind_str, True)}")
    # IF ONLY THE PLAN WAS ASKED FOR OR THERE IS NOTHING TO DO
    if args.list or not task_dict_list:
        # EXIT FUNCTION
        return
    # RECORD THE RUN (MANIFEST) AND HOLD THE VIX_FAMILY LOCK WHILE STAGING FILES ARE WRITTEN
    with ScriptRun("download_ibkr_index", vars(args), "vix_family", break_lock_bool_in=args.break_lock) as run:
        # CONNECT
        app = IndexApp()
        if not app.connect_app(args.host, args.port, args.client_id):
            raise SystemExit(1)
        # DOWNLOAD, THEN ALWAYS DISCONNECT
        try:
            summary_pdf = download_index_task_pdf(app, task_dict_list, max_in_flight_int_in=args.max_in_flight, run_id_str_in=run.run_id_str)
        finally:
            app.disconnect_app()
        # RECORD THE COUNTS IN THE MANIFEST; TASKS NOT SAVED (GAVE UP) OR NEVER FINISHED (CONNECTION LOST) ARE PROBLEMS
        run.summary_dict["status_count_dict"] = summary_pdf["status_str"].value_counts().to_dict() if not summary_pdf.empty else {}
        problem_int = int((~summary_pdf["saved_bool"].astype(bool)).sum()) + (len(task_dict_list) - len(summary_pdf))
        run.summary_dict["problem_count_int"] = problem_int
        if problem_int:
            run.status_override_str = "finished_with_problems"
    # DISPLAY THE PROBLEMS
    problem_pdf = summary_pdf[summary_pdf["status_str"] != "complete"]
    print(f"\nNot complete: {len(problem_pdf)}")
    print(problem_pdf.to_string(index=False)) if not problem_pdf.empty else None
    print("Next: python scripts/ingest/merge_index_staging_into_raw.py   (dry run), then add --apply")
    # EXIT 2 WHEN A TASK WAS NOT SAVED
    if problem_int:
        print(f"⚠️ {problem_int} request(s) not saved: see the download log. Exit code 2.")
        raise SystemExit(2)

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
