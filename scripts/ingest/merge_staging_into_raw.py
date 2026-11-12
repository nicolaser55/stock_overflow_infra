import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
# IMPORT THE MAINTENANCE FUNCTIONS
from ingest.raw_maintenance import merge_staging_into_raw_pdf

"""
Check the staged downloads and add them to the raw folder (ADD-ONLY: existing raw files and dates are never changed)

    python scripts/ingest/merge_staging_into_raw.py                      # DRY RUN: check every staged day, write nothing
    python scripts/ingest/merge_staging_into_raw.py --apply              # add the accepted days to the raw folder
    python scripts/ingest/merge_staging_into_raw.py --apply --include-partial

A day is added when it is a closed NYSE session, every minute is present (or --include-partial), the candlesticks pass
the repo's sanity check, and the raw folder does not have that date yet. Added files are moved to
<staging>/merged/ and listed in <staging>/merge_log_v2.csv. Afterwards: optional 'python scripts/ingest/organize_ohlcv_data.py',
then the repo's bad tick check and pipeline step 00 in stock_overflow_workspace.
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and runs the merge.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Add checked staging day files to the raw folder (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really copy the files (default: dry run)")
    parser.add_argument("--include-partial", action="store_true", help="also add sessions with missing minutes")
    parser.add_argument("--staging", default=config.STAGING_OHLCV_PATH_STR)
    parser.add_argument("--raw", default=config.RAW_OHLCV_PATH_STR)
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # DEFINE THE FOLDERS (ENDING WITH "/"); BOTH MUST LIE INSIDE THE QUARANTINE'S RAW ZONE, NEVER IN A DEVELOPMENT VIEW
    staging_path_str = config.check_write_path_str(args.staging, args.skip_root_check)
    raw_path_str = config.check_write_path_str(args.raw, args.skip_root_check)
    # RUN THE MERGE
    with ScriptRun("merge_staging_into_raw", vars(args), "spy_1min", record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        result_pdf = merge_staging_into_raw_pdf(args.apply, args.include_partial, staging_path_str, raw_path_str,
                                                f"{staging_path_str}merged/", f"{staging_path_str}merge_log_v2.csv", run_id_str_in=run.run_id_str)
        run.summary_dict["decision_count_dict"] = result_pdf["decision_str"].value_counts().to_dict() if not result_pdf.empty else {}
    # DISPLAY THE RESULT
    print(result_pdf.to_string(index=False)) if not result_pdf.empty else None

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
