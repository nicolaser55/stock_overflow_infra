import json
import argparse
# IMPORT THE CONFIGURATION, THE RUN RECORDS AND THE FRED MODULE
from ingest import config
from ingest.records import ScriptRun
from ingest import fred

"""
Download a FRED / ALFRED series with every vintage and publish its first-release and revision rows (APPEND-ONLY)

    python scripts/ingest/download_fred_series.py                  # DRY RUN: fetch, build, check, compare; write nothing
    python scripts/ingest/download_fred_series.py --apply          # also save the pages and add the new rows

Series: DTB3 (default) -> store01_rawzone/fred_dtb3/dtb3.csv. The API key is read from Windows Credential Manager
(scripts/machine/store_fred_key.py). Output is counts only; a date is shown only if it is before print_dates_before (config/local.toml).
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and runs each series.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Fetch a FRED series with its vintages and publish new rows (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--series", nargs="*", default=list(fred.FRED_SERIES_SPEC_DICT), choices=list(fred.FRED_SERIES_SPEC_DICT))
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE
    config.check_target_root_str(args.skip_root_check)
    # RUN EACH SERIES UNDER ITS OWN LOCK AND MANIFEST
    stopped_bool = False
    for series_str in args.series:
        lock_str = fred.FRED_SERIES_SPEC_DICT[series_str]["lock_str"]
        with ScriptRun("download_fred_series", vars(args), lock_str, record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
            try:
                summary_dict = fred.run_fred_series_dict(series_str, args.apply, run.run_id_str)
            except fred.FredError as error:
                print(f"❌ {series_str}: {error}")
                raise SystemExit(1) from None
            run.summary_dict[series_str] = summary_dict
        # DISPLAY THE SUMMARY (COUNTS ONLY)
        print(f"\n=== {series_str} ({'APPLY' if args.apply else 'DRY RUN, nothing written'}) ===")
        print(json.dumps(summary_dict, indent=2))
        if "stopped_str" in summary_dict:
            print(f"❌ {series_str} not published: {summary_dict['stopped_str']}")
            stopped_bool = True
        else:
            print(f"✅ {series_str}: {summary_dict['row_to_add_count_int']} row(s) {'added' if args.apply else 'would be added'}")
    # EXIT 1 IF A SERIES WAS STOPPED
    raise SystemExit(1 if stopped_bool else 0)

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
