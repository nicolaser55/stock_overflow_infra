import argparse
# IMPORT THE INGEST CONFIGURATION
from ingest import config
# IMPORT THE RUN RECORDS (MANIFEST AND LOCKS)
from ingest.records import ScriptRun, SourceLock, LockHeldError
# IMPORT THE RESTORE OF A MERGE STEP
from ingest.raw_files import restore_pending_journal_dict

"""
Put back the files of a merge step that was interrupted (organize, clean, daily index merge) from its journal

A merge step moves its source files into a backup folder before it writes the target. If the process is killed in between (closed
window, reboot, power cut), the raw folder lacks those rows and a journal stays in <root>/store01_rawzone/_ingest/pending/. Every
other script then refuses to run. This script returns the folder to its state before the step: the sources moved back, the
target restored from its byte copy (or removed when it did not exist before), then the journal removed.

    python scripts/ingest/restore_pending_merge.py            # DRY RUN: what would be put back (counts only)
    python scripts/ingest/restore_pending_merge.py --apply    # put the files back and remove the journals

A journal whose files are missing, or present in both places, is refused and kept: look at it by hand (it lists paths only).
Re-run the interrupted script afterwards (dry run first).
"""

# FUNCTION: MAIN
def main():
    """
    Restores every pending journal (oldest first) while holding both raw-folder locks.
    """
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Put back the files of interrupted merge steps (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="really put the files back (default: dry run)")
    parser.add_argument("--skip-root-check", action="store_true", help="bypass the target root check (tests on temporary folders only)")
    parser.add_argument("--break-lock", action="store_true", help="remove a lock left by a run that is gone (crash, closed window)")
    args = parser.parse_args()
    # STOP UNLESS THE TARGET ROOT IS USABLE (PENDING JOURNALS ARE WHAT THIS SCRIPT IS FOR)
    config.check_target_root_str(args.skip_root_check, allow_pending_bool_in=True)
    # LIST THE JOURNALS
    journal_str_list = config.get_pending_journal_list()
    print(f"Pending merge journals: {len(journal_str_list)}")
    if not journal_str_list:
        return
    # HOLD BOTH RAW-FOLDER LOCKS (A JOURNAL MAY BELONG TO THE SPY OR TO AN INDEX FOLDER)
    refused_int = 0
    with ScriptRun("restore_pending_merge", vars(args), "spy_1min", record_bool_in=args.apply, break_lock_bool_in=args.break_lock) as run:
        index_lock = SourceLock("vix_family", run.run_id_str, f"{config.INGEST_RECORD_PATH_STR}locks/", args.break_lock) if args.apply else None
        try:
            if index_lock:
                index_lock.acquire()
        except LockHeldError as error:
            print(f"❌ {error}")
            raise SystemExit(1) from None
        try:
            for journal_str in journal_str_list:
                result_dict = restore_pending_journal_dict(journal_str, args.apply)
                refused_int += result_dict["status_str"] == "refused"
        finally:
            if index_lock:
                index_lock.release()
        run.summary_dict.update(journal_count_int=len(journal_str_list), refused_count_int=refused_int)
    # REPORT
    print(f"{'' if args.apply else '🔎 [DRY RUN] '}{len(journal_str_list) - refused_int} journal(s) {'restored' if args.apply else 'restorable'}, {refused_int} refused")
    if refused_int:
        print(f"❌ A refused journal stays in {config.PENDING_PATH_STR}: its files are missing or in both places. Look at it by hand before any other run.")
        raise SystemExit(1)

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # RUN THE MAIN FUNCTION
    main()
