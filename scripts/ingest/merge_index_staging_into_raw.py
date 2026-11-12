import argparse
# IMPORT THE INDEX CONFIGURATION AND MERGE
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCK)
from ingest.records import ScriptRun
from ingest import index_config
from ingest.index_merge import merge_index_staging_into_raw_pdf

"""
Check the staged VIX / VIX3M files and add them to the raw folders (ADD-ONLY: raw dates are never replaced)

    python scripts/ingest/merge_index_staging_into_raw.py                   # DRY RUN: check every staged file, write nothing
    python scripts/ingest/merge_index_staging_into_raw.py --apply           # add the accepted files
    python scripts/ingest/merge_index_staging_into_raw.py --apply --include-partial

1-minute files are copied as new day files (a date already in raw is skipped). Daily files are merged into the raw file of the same year with the
text-preserving engine (rows already in raw win; backup first, into <index raw root>_backup_<time>/<leaf>/). Afterwards the folders can be rolled up with
'python scripts/ingest/organize_ohlcv_data.py --raw <folder>' (dry run first): day files of closed months become month files, month files of closed years year files.
"""

# FUNCTION: MAIN
def main():
    """
    Parses the arguments and merges every index and bar kind.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Add checked VIX / VIX3M staging files to the raw folders (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--include-partial", action="store_true", help="also add 1-minute sessions with missing minutes")
    parser.add_argument("--symbols", nargs="*", default=list(index_config.INDEX_SPEC_DICT), choices=list(index_config.INDEX_SPEC_DICT))
    parser.add_argument("--bars", nargs="*", default=list(index_config.INDEX_BAR_KIND_DICT), choices=list(index_config.INDEX_BAR_KIND_DICT))
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE (NOT A DEVELOPMENT VIEW); --skip-root-check IS FOR TESTS
    config.check_target_root_str(args.skip_root_check)
    # MERGE EVERY INDEX AND BAR KIND
    failed_int = 0
    with ScriptRun("merge_index_staging_into_raw", vars(args), "vix_family", record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        for symbol_str in args.symbols:
            for bar_kind_str in args.bars:
                result_pdf = merge_index_staging_into_raw_pdf(symbol_str, bar_kind_str, args.apply, args.include_partial, run_id_str_in=run.run_id_str)
                run.summary_dict[f"{symbol_str}_{bar_kind_str}"] = result_pdf["decision_str"].str.split(":").str[0].value_counts().to_dict() if not result_pdf.empty else {}
                print(result_pdf.to_string(index=False)) if not result_pdf.empty else None
                # A DAILY MERGE THAT FAILED OR ROLLED BACK (FOLDER RESTORED) IS A PROBLEM, NOT A NORMAL SKIP
                failed_int += int(result_pdf["decision_str"].str.match(r"skip: (failed|rolled_back)").sum()) if not result_pdf.empty else 0
        run.summary_dict["problem_count_int"] = failed_int
        if failed_int:
            run.status_override_str = "finished_with_problems"
    # EXIT 2 WHEN A MERGE FAILED
    if failed_int:
        print(f"⚠️ {failed_int} daily merge(s) failed and were rolled back (raw unchanged for them). Exit code 2.")
        raise SystemExit(2)

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
