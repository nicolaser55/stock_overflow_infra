import os
import re
import sys
import json
import subprocess

"""
Repository Guard (this repository is public)

Run from the repository root:   python tests/test_repo_guard.py   (also part of tests/run_all_tests.py and of CI)

What is verified, on every file git tracks (or every file of the folder when git is not available):
    1. No data: no CSV, Parquet, pickle, Excel or database files, no zip archives, nothing in a logs/ or probe_output/ folder.
    2. No notebook with outputs (a notebook may be committed only with every output cleared).
    3. No machine settings: config/local.toml is never tracked.
    4. No machine-specific text: no IPv4 address other than 127.0.0.1 / 0.0.0.0, no network share path (\\\\host\\share or
       //host/share), no Windows user-profile path, no Tailscale machine name.
    5. No credentials: no private key block, no "password=..." / "api_key=..." value, no IBKR account id.
    6. The ingest does not import the research package (so).
A finding prints the file and line, never the matched value.
"""

# DEFINE THE REPOSITORY ROOT
ROOT_PATH_STR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DEFINE THE FORBIDDEN FILE KINDS
DATA_SUFFIX_TUPLE = (".csv", ".parquet", ".pkl", ".pickle", ".xlsx", ".xls", ".db", ".sqlite", ".feather", ".h5", ".zip", ".7z", ".log")
DATA_FOLDER_STR_LIST = ["logs/", "probe_output/", "landing/", "_ingest/"]
# DEFINE THE FORBIDDEN TEXT (NAME -> PATTERN)
TEXT_RULE_DICT = {
    "IPv4 address": re.compile(r"(?<![\d.])(?!127\.0\.0\.1(?![\d.]))(?!0\.0\.0\.0(?![\d.]))(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "network share path": re.compile(r"(?<![\w:])(?:\\\\|//)(?!/)[A-Za-z0-9][\w.-]*[\\/][\w$.-]+"),
    "user profile path": re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+(?!<|\$|%|Public)[\w.-]+"),
    "Tailscale name": re.compile(r"(?i)\b[\w-]+\.ts\.net\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "credential value": re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|apikey|app_password|secret)\s*[=:]\s*['\"]?(?!\*\*\*|<|\{|\$|os\.|None|\"\"|''|getpass|keyring)[A-Za-z0-9_\-/+]{6,}"),
    "IBKR account id": re.compile(r"\b(?:DU|DF|U|F)\d{7,8}\b"),
}
# DEFINE THE FILES WHOSE TEXT IS NOT CHECKED (THIS FILE HOLDS THE PATTERNS AND SAMPLES; .gitleaks.toml HOLDS PATTERNS)
TEXT_EXEMPT_STR_LIST = ["tests/test_repo_guard.py", ".gitleaks.toml"]
# DEFINE THE FAKE VALUES THE TESTS USE ON PURPOSE: ONLY THESE EXACT VALUES ARE IGNORED, NEVER A WHOLE FILE (A REAL VALUE PASTED INTO A TEST
# FILE IS FOUND LIKE ANYWHERE ELSE)
FAKE_VALUE_RX = re.compile(r"(?:api_key=0123abcd|password: hunter2)(?![A-Za-z0-9])|\b(?:DU|DF|U|F)0000000\b")
# DEFINE THE IMPORT OF THE RESEARCH PACKAGE
SO_IMPORT_RX = re.compile(r"^\s*(?:from\s+so(?:\.|\s+import)|import\s+so(?:\.|\s|$))", re.M)

# FUNCTION: LIST THE FILES TO CHECK
def get_file_str_list():
    """
    Returns:
        list[str]: Repository-relative paths with "/" (git ls-files, or a folder walk without .git and venvs)
    """
    # ASK GIT
    try:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT_PATH_STR, capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            return sorted(path_str for path_str in result.stdout.decode("utf-8").split("\0") if path_str)
    except (OSError, subprocess.SubprocessError):
        pass
    # WALK THE FOLDER
    file_str_list = []
    for folder_str, dir_list, name_list in os.walk(ROOT_PATH_STR):
        dir_list[:] = [d for d in dir_list if d not in {".git", "__pycache__"} and not d.startswith("venv") and not d.endswith(".egg-info")]
        file_str_list += [os.path.relpath(os.path.join(folder_str, name_str), ROOT_PATH_STR).replace("\\", "/") for name_str in name_list]
    # WITHOUT GIT, SKIP THE MACHINE SETTINGS FILE (GIT-IGNORED; THE TRACKED-FILE CHECK NEEDS GIT)
    file_str_list = [path_str for path_str in file_str_list if path_str != "config/local.toml"]
    return sorted(file_str_list)

# FUNCTION: COLLECT THE FINDINGS
def get_finding_str_list():
    """
    Returns:
        list[str]: One line per finding ("path:line: rule"); empty when the repository is clean
    """
    # LIST TO HOLD THE FINDINGS
    finding_str_list = []
    # ITERATE OVER THE FILES
    for path_str in get_file_str_list():
        full_path_str = os.path.join(ROOT_PATH_STR, path_str)
        if not os.path.isfile(full_path_str):
            continue
        # DATA FILES AND FOLDERS
        if path_str.lower().endswith(DATA_SUFFIX_TUPLE) or any(f"/{folder_str}" in f"/{path_str}" for folder_str in DATA_FOLDER_STR_LIST):
            finding_str_list.append(f"{path_str}: data file or data folder")
            continue
        # MACHINE SETTINGS
        if path_str == "config/local.toml":
            finding_str_list.append(f"{path_str}: machine settings must stay untracked")
            continue
        # READ THE TEXT (BINARY FILES ARE REPORTED: NOTHING BINARY BELONGS HERE)
        try:
            text_str = open(full_path_str, encoding="utf-8").read()
        except UnicodeDecodeError:
            finding_str_list.append(f"{path_str}: binary file")
            continue
        # NOTEBOOK OUTPUTS
        if path_str.endswith(".ipynb"):
            cell_list = json.loads(text_str).get("cells", [])
            if any(cell.get("outputs") for cell in cell_list) or any(cell.get("execution_count") for cell in cell_list):
                finding_str_list.append(f"{path_str}: notebook with outputs (clear every output before committing)")
        # IMPORTS OF THE RESEARCH PACKAGE
        if path_str.endswith(".py") and SO_IMPORT_RX.search(text_str):
            finding_str_list.append(f"{path_str}: imports the research package so")
        # TEXT RULES
        if path_str in TEXT_EXEMPT_STR_LIST:
            continue
        for line_int, line_str in enumerate(text_str.splitlines(), 1):
            line_str = FAKE_VALUE_RX.sub("<fake>", line_str)
            for rule_str, rule_rx in TEXT_RULE_DICT.items():
                if rule_rx.search(line_str):
                    finding_str_list.append(f"{path_str}:{line_int}: {rule_str}")
    # RETURN THE FINDINGS
    return finding_str_list

# FUNCTION: CHECK THE RULES ON KNOWN SAMPLES (THE PATTERNS FIND WHAT THEY SHOULD AND NOTHING ELSE)
def test_rule_samples():
    # SAMPLES THAT MUST BE FOUND
    for rule_str, sample_str in [("IPv4 address", "share at 100.64.1.2 here"), ("network share path", "root = '//myhost/data/'"),
                                 ("network share path", r"\\myhost\data"), ("user profile path", r"C:\Users\someone\Desktop"),
                                 ("Tailscale name", "box.tail1234.ts.net"), ("credential value", "api_key=abcdef123456"),
                                 ("IBKR account id", "account U1234567")]:
        assert TEXT_RULE_DICT[rule_str].search(sample_str), (rule_str, sample_str)
    # SAMPLES THAT MUST NOT BE FOUND
    for rule_str, sample_str in [("IPv4 address", "127.0.0.1"), ("IPv4 address", "version 10.45.1"), ("network share path", "https://github.com/x/y"),
                                 ("user profile path", r"C:\Users\<you>\Desktop"), ("credential value", "api_key=***"),
                                 ("credential value", 'password = os.environ["X"]'), ("IBKR account id", "U123")]:
        assert not TEXT_RULE_DICT[rule_str].search(sample_str), (rule_str, sample_str)
    # ONLY THE EXACT FAKE VALUES ARE IGNORED
    assert not TEXT_RULE_DICT["IBKR account id"].search(FAKE_VALUE_RX.sub("<fake>", "accounts DU0000000,U0000000"))
    assert TEXT_RULE_DICT["IBKR account id"].search(FAKE_VALUE_RX.sub("<fake>", "accounts DU0000000,U1234567"))
    assert TEXT_RULE_DICT["credential value"].search(FAKE_VALUE_RX.sub("<fake>", "api_key=0123abcdef99"))
    print("✅ guard rules: samples found and not found as expected; only the exact fake values are ignored")

# FUNCTION: CHECK THE REPOSITORY
def test_repository():
    # COLLECT THE FINDINGS
    finding_str_list = get_finding_str_list()
    # FAIL WITH EVERY FINDING (PATH AND LINE ONLY)
    assert not finding_str_list, "Repository guard findings:\n" + "\n".join(finding_str_list)
    print(f"✅ repository guard: {len(get_file_str_list())} files checked, no data, outputs, machine settings, machine-specific text, credentials or so imports")

# RUN THE TESTS
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_rule_samples()
    test_repository()
