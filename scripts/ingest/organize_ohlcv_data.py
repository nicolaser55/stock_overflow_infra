import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
# IMPORT THE MAINTENANCE FUNCTIONS
from ingest.raw_maintenance import organize_raw_pdf

"""
Roll up the raw folder: day files of closed months -> month files; day / month files of closed years -> year files

    python scripts/ingest/organize_ohlcv_data.py            # DRY RUN
    python scripts/ingest/organize_ohlcv_data.py --apply

Text is kept exactly, rows already in a month / year file win over a duplicate, every changed file is backed up into
<raw>_backup_YYYYMMDD_HHMMSS/, and a target is all-or-nothing (see ingest/raw_files.py). "Closed" uses the real New York
clock (the old script used the laptop's clock labelled as New York time).
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and organizes the folder.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Roll up day / month files of closed periods (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--raw", default=config.RAW_OHLCV_PATH_STR)
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # THE FOLDER MUST LIE INSIDE THE QUARANTINE'S RAW ZONE, NEVER IN A DEVELOPMENT VIEW
    raw_path_str = config.check_write_path_str(args.raw, args.skip_root_check)
    # ORGANIZE THE FOLDER
    with ScriptRun("organize_ohlcv_data", vars(args), "spy_1min", record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        result_pdf = organize_raw_pdf(args.apply, raw_path_str)
        run.summary_dict["target_count_int"] = len(result_pdf)
    # DISPLAY THE RESULT
    print(result_pdf.to_string(index=False)) if not result_pdf.empty else None

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
