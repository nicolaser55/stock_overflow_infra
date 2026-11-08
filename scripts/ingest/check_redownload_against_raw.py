import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE COMPARISON
from ingest.raw_maintenance import get_redownload_comparison_pdf

"""
Compare sessions downloaded again with the same sessions in the SPY raw folder (read only; prints counts, never prices)

Use it to check the download end to end on dates the research may see (e.g. 2005), without touching raw or staging:

    python scripts/ingest/download_ibkr_ohlcv.py --dates 2005-01-03 2005-01-04 --redownload --staging <root>/store01_rawzone/_ingest/check_redownload/
    python scripts/ingest/check_redownload_against_raw.py --check <root>/store01_rawzone/_ingest/check_redownload/

For each session: minutes in raw, in the new download, in both, in only one; minutes whose five values are written identically,
have the same numbers, or differ (a difference means IBKR's history changed since the raw file was downloaded, or a bad-tick
correction was applied to raw).
"""

# FUNCTION: MAIN
def main():
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Compare re-downloaded sessions with the SPY raw folder (read only).")
    parser.add_argument("--check", required=True, help="folder holding the re-downloaded day files")
    parser.add_argument("--raw", default=config.RAW_OHLCV_PATH_STR)
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS USABLE
    config.check_target_root_str(args.skip_root_check)
    # COMPARE
    get_redownload_comparison_pdf(args.check.replace("\\", "/").rstrip("/") + "/", args.raw.replace("\\", "/").rstrip("/") + "/")

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    main()
