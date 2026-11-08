import argparse
import subprocess
import sys
from pathlib import Path

"""
Create the Python 3.12 virtual environment of stock_overflow_infra (repeatable; nothing is asked interactively).

    python setup_venv.py                                   # venv-infra, ibapi from C:/TWS API/source/pythonclient
    python setup_venv.py --name venv-infra --ibapi-source "C:/TWS API/source/pythonclient"
    python setup_venv.py --ci                              # ibapi 9.81.1.post1 from PyPI instead of the TWS API source (CI)

Steps (an existing venv of the right Python version is reused):
    1. create <repo>/<name> with Python 3.12 (Windows: the "py -3.12" launcher);
    2. upgrade pip, install requirements/infra_requirements.txt (or requirements/ci_requirements.txt with --ci);
    3. install ibapi from the TWS API source folder (ibapi 10.45.1 is not on PyPI), unless --ci;
    4. install this repository in editable mode (package ingest), so the scripts import it from any folder;
    5. print the installed versions.
The research workspace is NOT installed: this repository does not import it.
"""

# DEFINE THE SETTINGS
PYTHON_VERSION_STR = "3.12"
DEFAULT_VENV_NAME_STR = "venv-infra"
DEFAULT_IBAPI_SOURCE_STR = "C:/TWS API/source/pythonclient"
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
    # PARSE THE ARGUMENTS
    parser = argparse.ArgumentParser(description="Create the stock_overflow_infra venv.")
    parser.add_argument("--name", default=DEFAULT_VENV_NAME_STR, help="venv folder name, created in the repository root")
    parser.add_argument("--ibapi-source", default=DEFAULT_IBAPI_SOURCE_STR, help="TWS API python source folder (holds the ibapi package)")
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
        ibapi_source_path = Path(args.ibapi_source)
        if not (ibapi_source_path / "ibapi").is_dir():
            print(f"❌ No ibapi package in {ibapi_source_path} (install the TWS API, or pass --ibapi-source).", file=sys.stderr)
            raise SystemExit(1)
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
