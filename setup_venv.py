import re
import glob
import argparse
import subprocess
import sys
from pathlib import Path

"""
Create the Python 3.12 virtual environment of stock_overflow_infra (repeatable; nothing is asked interactively).

    python setup_venv.py                                   # venv-infra, ibapi from the newest C:/TWS API*/source/pythonclient
    python setup_venv.py --name venv-infra --ibapi-source "C:/TWS API 1050.02/source/pythonclient"
    python setup_venv.py --ci                              # ibapi 9.81.1.post1 from PyPI instead of the TWS API source (CI)

Steps (an existing venv of the right Python version is reused):
    1. create <repo>/<name> with Python 3.12 (Windows: the "py -3.12" launcher);
    2. upgrade pip, install requirements/infra_requirements.txt (or requirements/ci_requirements.txt with --ci);
    3. install ibapi from the TWS API source folder (official ibapi 10.x is not on PyPI), unless --ci; the folder must hold ibapi
       10.50.1 or later (the oldest IBKR supports from 2026-12-15; ingest.config.MIN_IBAPI_VERSION_STR);
    4. install this repository in editable mode (package ingest), so the scripts import it from any folder;
    5. print the installed versions.
The research workspace is NOT installed: this repository does not import it.
"""

# DEFINE THE SETTINGS
PYTHON_VERSION_STR = "3.12"
DEFAULT_VENV_NAME_STR = "venv-infra"
# DEFINE WHERE THE TWS API INSTALLERS PUT THE PYTHON SOURCE (EACH VERSION IN ITS OWN FOLDER, E.G. "C:/TWS API 1050.02"; THE NEWEST IS USED)
IBAPI_SOURCE_GLOB_STR = "C:/TWS API*/source/pythonclient"
# DEFINE THE OLDEST ibapi ACCEPTED (THE SAME AS ingest.config.MIN_IBAPI_VERSION_STR; THIS SCRIPT RUNS BEFORE THE PACKAGE IS INSTALLED)
MIN_IBAPI_VERSION_TUP = (10, 50, 1)
BASE_PATH = Path(__file__).resolve().parent

# FUNCTION: GET THE VENV PYTHON
def get_venv_python_path(venv_path_in):
    """
    Args:
        venv_path_in (Path): Venv folder

    Returns:
        Path: Python executable of the venv
    """
    return venv_path_in / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

# FUNCTION: READ THE ibapi VERSION OF A SOURCE FOLDER
def get_source_version_tup(source_path_in):
    """
    Args:
        source_path_in (Path): pythonclient folder

    Returns:
        tuple: (major, minor, micro), or () when the folder holds no readable ibapi
    """
    init_path = Path(source_path_in) / "ibapi" / "__init__.py"
    if not init_path.is_file():
        return ()
    match = re.search(r'"major":\s*(\d+),\s*"minor":\s*(\d+),\s*"micro":\s*(\d+)', init_path.read_text(encoding="utf-8", errors="replace"))
    return tuple(int(number_str) for number_str in match.groups()) if match else ()

# FUNCTION: FIND THE NEWEST TWS API PYTHON SOURCE
def get_newest_source_path():
    """
    Returns:
        Path | None: The pythonclient folder with the highest ibapi version under IBAPI_SOURCE_GLOB_STR (None when there is none)
    """
    candidate_list = [(get_source_version_tup(path_str), Path(path_str)) for path_str in glob.glob(IBAPI_SOURCE_GLOB_STR)]
    candidate_list = [candidate for candidate in candidate_list if candidate[0]]
    return max(candidate_list)[1] if candidate_list else None

# FUNCTION: RUN A COMMAND AND STOP ON FAILURE
def run(command_list_in):
    """
    Args:
        command_list_in (list[str]): Command
    """
    print(f"> {' '.join(command_list_in)}", flush=True)
    result = subprocess.run(command_list_in)
    if result.returncode != 0:
        print(f"❌ Failed (exit code {result.returncode})", file=sys.stderr)
        raise SystemExit(result.returncode)

# FUNCTION: MAIN
def main():
    # PRINT IN UTF-8 EVEN THROUGH A cp1252 PIPE (THE STATUS SYMBOLS WOULD OTHERWISE STOP THE SCRIPT AT ITS LAST LINE)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Create the stock_overflow_infra venv.")
    parser.add_argument("--name", default=DEFAULT_VENV_NAME_STR, help="venv folder name, created in the repository root")
    parser.add_argument("--ibapi-source", default=None, help="TWS API python source folder (default: the newest C:/TWS API*/source/pythonclient)")
    parser.add_argument("--ci", action="store_true", help="install ibapi from PyPI (9.81.1.post1) instead of the TWS API source")
    args = parser.parse_args()
    # CREATE OR REUSE THE VENV
    venv_path = BASE_PATH / args.name
    python_path = get_venv_python_path(venv_path)
    if not python_path.is_file():
        launcher_list = ["py", f"-{PYTHON_VERSION_STR}"] if sys.platform == "win32" else [f"python{PYTHON_VERSION_STR}"]
        run([*launcher_list, "-m", "venv", str(venv_path)])
    version_str = subprocess.run([str(python_path), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                                 capture_output=True, text=True).stdout.strip()
    if version_str != PYTHON_VERSION_STR:
        print(f"❌ {venv_path} uses Python {version_str or 'unknown'}, not {PYTHON_VERSION_STR}: delete it or choose another --name.", file=sys.stderr)
        raise SystemExit(1)
    # INSTALL THE REQUIREMENTS
    run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_path), "-m", "pip", "install", "-r", str(BASE_PATH / "requirements" / ("ci_requirements.txt" if args.ci else "infra_requirements.txt"))])
    # INSTALL IBAPI FROM THE TWS API SOURCE
    if not args.ci:
        ibapi_source_path = Path(args.ibapi_source) if args.ibapi_source else get_newest_source_path()
        version_tup = get_source_version_tup(ibapi_source_path) if ibapi_source_path else ()
        if not version_tup:
            print(f"❌ No ibapi package in {ibapi_source_path or IBAPI_SOURCE_GLOB_STR} (install the TWS API, or pass --ibapi-source).", file=sys.stderr)
            raise SystemExit(1)
        if version_tup < MIN_IBAPI_VERSION_TUP:
            print(f"❌ {ibapi_source_path} holds ibapi {'.'.join(map(str, version_tup))}; at least {'.'.join(map(str, MIN_IBAPI_VERSION_TUP))} is needed.", file=sys.stderr)
            raise SystemExit(1)
        print(f"ibapi {'.'.join(map(str, version_tup))} from {ibapi_source_path}", flush=True)
        run([str(python_path), "-m", "pip", "install", str(ibapi_source_path)])
    # INSTALL THIS REPOSITORY IN EDITABLE MODE
    run([str(python_path), "-m", "pip", "install", "-e", str(BASE_PATH)])
    # PRINT THE VERSIONS
    run([str(python_path), "-c", "import sys, ibapi, pandas, pandas_market_calendars as m; "
                                 "print('python', sys.version.split()[0], '| ibapi', ibapi.get_version_string(), '| pandas', pandas.__version__, '| pandas_market_calendars', m.__version__)"])
    print(f"✅ venv ready: {python_path}")

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    main()
