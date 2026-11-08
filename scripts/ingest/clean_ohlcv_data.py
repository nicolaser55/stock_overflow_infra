import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
# IMPORT THE MAINTENANCE FUNCTIONS
from ingest.raw_maintenance import clean_raw_orphans_pdf

"""
Repair: fold left-over day / month files into the month / year file that already exists for them

    python scripts/ingest/clean_ohlcv_data.py               # DRY RUN
    python scripts/ingest/clean_ohlcv_data.py --apply

A left-over (orphan) file is a day or month file whose data was rolled up, but which could not be deleted. The pipeline
reads every file, so its rows would be counted twice. Rows are compared by exact instant; the row already in the month /
year file wins. The old script defaulted to DRY_RUN = False; this one is a dry run unless --apply is given.
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and cleans the folder.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Fold orphan day / month files into their existing month / year file (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--raw", default=config.RAW_OHLCV_PATH_STR)
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # CLEAN THE FOLDER
    with ScriptRun("clean_ohlcv_data", vars(args), "spy_1min", record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        result_pdf = clean_raw_orphans_pdf(args.apply, args.raw.replace("\\", "/").rstrip("/") + "/")
        run.summary_dict["target_count_int"] = len(result_pdf)
    # DISPLAY THE RESULT
    print(result_pdf.to_string(index=False)) if not result_pdf.empty else None

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
