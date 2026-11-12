import json
import argparse
# IMPORT THE CONFIGURATION, THE RUN RECORDS AND THE DIVIDEND MODULE
from ingest import config
from ingest.records import ScriptRun
from ingest import dividends

"""
Import SPY dividends from State Street's historical-distributions workbook, cross-checked against Yahoo Finance (APPEND-ONLY)

    python scripts/ingest/import_spy_dividends.py --ssga-file <xlsx>                              # DRY RUN: read, cross-check, check; write nothing
    python scripts/ingest/import_spy_dividends.py --ssga-file <xlsx> --apply                      # also keep the inputs and add the new rows
    python scripts/ingest/import_spy_dividends.py --ssga-file <xlsx> --yahoo-file <csv> ...     # a saved cross-check CSV

The workbook is "SPDR Historical Distributions" (XLSX) on ssga.com's SPDR Dividend Distributions page. Output is counts only;
a date is shown only if it is before print_dates_before (config/local.toml); amounts are never shown.
"""

# FUNCTION: MAIN
def main():
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Import SPY dividends (State Street, cross-checked against Yahoo Finance); dry run by default.")
    parser.add_argument("--ssga-file", required=True, help="State Street 'SPDR Historical Distributions' XLSX as downloaded")
    parser.add_argument("--yahoo-file", default=None, help="CSV ex_date,amount_usd to use instead of fetching Yahoo Finance (e.g. an archived run's yahoo_spy_dividends.csv)")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--skip-close-check", action="store_true", help="skip the amount / previous close sanity check (no raw SPY bars)")
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS SET, EXISTS AND IS THE QUARANTINE
    config.check_target_root_str(args.skip_root_check)
    # RUN UNDER THE LOCK AND MANIFEST
    with ScriptRun("import_spy_dividends", vars(args), dividends.DIVIDEND_LOCK_STR, record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        try:
            summary_dict = dividends.run_dividend_dict(args.ssga_file, args.apply, run.run_id_str, crosscheck_file_path_str_in=args.yahoo_file,
                                                       check_close_bool_in=not args.skip_close_check)
        except dividends.DividendError as error:
            print(f"❌ SPY dividends: {error}")
            raise SystemExit(1) from None
        run.summary_dict["spy_dividends"] = summary_dict
        # A BLOCKING CHECK WROTE NOTHING: THE MANIFEST SAYS SO
        if "stopped_str" in summary_dict:
            run.status_override_str = "stopped"
    # DISPLAY THE SUMMARY (COUNTS ONLY)
    print(f"\n=== SPY dividends ({'APPLY' if args.apply else 'DRY RUN, nothing written'}) ===")
    print(json.dumps(summary_dict, indent=2))
    if "stopped_str" in summary_dict:
        print(f"❌ SPY dividends not published: {summary_dict['stopped_str']}")
        raise SystemExit(1)
    print(f"✅ SPY dividends: {summary_dict['row_to_add_count_int']} row(s) {'added' if args.apply else 'would be added'}")

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
