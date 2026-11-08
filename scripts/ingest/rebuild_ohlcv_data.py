import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
# IMPORT THE MAINTENANCE FUNCTIONS
from ingest.raw_maintenance import rebuild_raw_pdf

"""
Rebuild a folder into the organized layout (one file per year / closed month / day of the current month)

    python scripts/ingest/rebuild_ohlcv_data.py                                    # DRY RUN, raw -> <raw>_rebuilt/
    python scripts/ingest/rebuild_ohlcv_data.py --apply                            # write <raw>_rebuilt/
    python scripts/ingest/rebuild_ohlcv_data.py --apply --target <raw folder>      # in place (every replaced / removed file is backed up)

Rows of year files win over month files, which win over day files (the compiled files hold any correction). Text is kept
exactly. The old script wrote with DRY_RUN = False and deleted "stale" files by default; here both are opt-in.
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and rebuilds the folder.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Rebuild a folder into the organized layout (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--source", default=config.RAW_OHLCV_PATH_STR)
    parser.add_argument("--target", default=config.RAW_OHLCV_PATH_STR.rstrip("/") + "_rebuilt/")
    parser.add_argument("--delete-stale", action="store_true", help="move target OHLCV files that are not rebuilt to the backup folder")
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # REBUILD THE FOLDER
    with ScriptRun("rebuild_ohlcv_data", vars(args), "spy_1min", record_bool_in=args.apply, break_lock_bool_in=args.break_lock):
        rebuild_raw_pdf(args.source.replace("\\", "/").rstrip("/") + "/", args.target.replace("\\", "/").rstrip("/") + "/", args.apply, args.delete_stale)

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
