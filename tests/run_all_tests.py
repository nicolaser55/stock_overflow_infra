import os
import sys
import time
import tempfile
import subprocess

"""
Run Every Test Suite

Run from the repository root:   python tests/run_all_tests.py
Each suite runs in its own interpreter (plain asserts; a failing suite stops with its error). Takes about a minute.
No connection to IBKR and no access to any real data folder: every suite points SO_INGEST_TARGET_ROOT to its own temporary
folder before ingest is imported, and this runner also sets it to an empty temporary folder, so config/local.toml is never used.

    tests/test_repo_guard.py       this public repository holds no data, outputs, machine settings or credentials
    tests/test_ingest.py           IBKR client, sessions, SPY download, raw files, maintenance, reader compatibility
    tests/test_index_pipeline.py   VIX / VIX3M download and merge; target root check, run manifests, locks, log headers, secrets
    tests/test_fred.py             FRED / ALFRED vintages (DTB3): paging, first-release and revision rows, clocks, checks, append-only publish
    tests/test_dividends.py        SPY dividends: State Street workbook, Yahoo cross-check, resolutions, checks, append-only publish, revisions

Output is forced to UTF-8 (the suites print emoji; a Windows console or pipe in cp1252 would otherwise crash the print).
"""

# DEFINE THE SUITES
SUITE_LIST = ["test_repo_guard.py", "test_ingest.py", "test_index_pipeline.py", "test_fred.py", "test_dividends.py"]
# DEFINE THE TESTS FOLDER
TESTS_PATH_STR = os.path.dirname(os.path.abspath(__file__))

# RUN THE SUITES
if __name__ == "__main__":
    # FORCE UTF-8 OUTPUT IN THIS PROCESS AND IN THE SUITES; NEVER LET A SUITE FALL BACK TO THE MACHINE'S TARGET ROOT
    sys.stdout.reconfigure(encoding="utf-8")
    suite_env_dict = {**os.environ, "PYTHONIOENCODING": "utf-8", "SO_INGEST_TARGET_ROOT": tempfile.mkdtemp(prefix="so_ingest_runner_")}
    suite_env_dict.pop("SO_DATA_PATH", None)
    # ITERATE OVER THE SUITES
    total_start_time = time.time()
    for suite_str in SUITE_LIST:
        # DISPLAY INFORMATION
        print(f"\n===== {suite_str} =====", flush=True)
        start_time = time.time()
        # RUN THE SUITE
        result = subprocess.run([sys.executable, os.path.join(TESTS_PATH_STR, suite_str)], env=suite_env_dict)
        # STOP AT THE FIRST FAILURE
        if result.returncode != 0:
            print(f"\n❌ {suite_str} failed")
            raise SystemExit(result.returncode)
        # DISPLAY INFORMATION
        print(f"({time.time() - start_time:.0f} s)", flush=True)
    # DISPLAY THE RESULT
    print(f"\nAll test suites passed ✅ ({len(SUITE_LIST)} suites, {time.time() - total_start_time:.0f} s)")
