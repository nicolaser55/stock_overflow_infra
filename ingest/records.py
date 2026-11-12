import os
import re
import sys
import json
import socket
import hashlib
import secrets
import platform
import subprocess
import importlib.metadata
from datetime import datetime, timezone
# IMPORT THE INGEST CONFIGURATION
from ingest import config

"""
Run Records: run manifests, source locks, checksums and secret redaction

Every script that writes (a download, a merge, an organize, a clean, a rebuild) runs inside ScriptRun:
    - a run id (UTC time + random suffix, e.g. 20261108T163000Z_3fa2c1) is written into every log row of the run;
    - a manifest <root>/store01_rawzone/_ingest/runs/<run id>.json is written when the run starts ("running") and again when it
      ends ("finished" or "failed" with the error type), with: the script and its arguments, start and end time (UTC), the git
      commit of this repository and whether the clone had uncommitted changes, the Python and package versions, the target root,
      and a summary of the result (counts, never data values);
    - a lock file <root>/store01_rawzone/_ingest/locks/<lock name>.lock stops a second run (on this machine or the other one)
      from writing the same folders at the same time. A lock left by a crashed run is reported with its holder; remove it with
      --break-lock once you know that run is gone.

Nothing here prints or stores a credential: account data is never requested, and redact_str() masks any "api_key=..." style
value before text is logged or written into a manifest.
"""

# DEFINE THE PACKAGES WHOSE VERSIONS ARE RECORDED IN A MANIFEST
MANIFEST_PACKAGE_STR_LIST = ["pandas", "numpy", "pandas_market_calendars", "ibapi", "protobuf"]
# DEFINE THE PATTERN OF SECRET VALUES IN URLS, LOGS AND COMMAND LINES (key=value or key: value)
SECRET_RX = re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|passwd|secret|app_password)(\s*[=:]\s*)([^&\s'\",;]+)")

# FUNCTION: MASK SECRET VALUES IN A TEXT
def redact_str(text_str_in):
    """
    Args:
        text_str_in (str): Text (URL, log line, command line)

    Returns:
        str: The text with every secret value replaced by "***"
    """
    # REPLACE THE VALUES, THEN ANY ACCOUNT ID
    return config.ACCOUNT_RX.sub("***", SECRET_RX.sub(lambda match: f"{match.group(1)}{match.group(2)}***", str(text_str_in)))

# FUNCTION: GET THE SHA-256 OF A FILE
def get_file_sha256_str(file_path_str_in):
    """
    Args:
        file_path_str_in (str): File path

    Returns:
        str: Hexadecimal SHA-256 of the file bytes
    """
    # HASH THE FILE IN CHUNKS
    digest = hashlib.sha256()
    with open(file_path_str_in, "rb") as file:
        for chunk_bytes in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk_bytes)
    # RETURN THE DIGEST
    return digest.hexdigest()

# FUNCTION: GET A NEW RUN ID
def get_run_id_str(now_utc_in=None):
    """
    Args:
        now_utc_in (datetime | None): Current UTC time (None = the real clock)

    Returns:
        str: Run id "YYYYMMDDTHHMMSSZ_xxxxxx"
    """
    # BUILD THE ID FROM THE UTC TIME AND A RANDOM SUFFIX
    now_utc = now_utc_in or datetime.now(timezone.utc)
    return f"{now_utc.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"

# FUNCTION: GET THE GIT STATE OF THIS REPOSITORY
def get_git_state_dict(repo_path_str_in=config.REPO_ROOT_PATH_STR):
    """
    Args:
        repo_path_str_in (str): Repository root

    Returns:
        dict: commit_str ("" when git is not available) and dirty_bool (uncommitted changes in tracked or untracked files)
    """
    # ASK GIT (A MISSING GIT OR A FOLDER THAT IS NOT A CLONE GIVES AN EMPTY COMMIT)
    try:
        commit_str = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path_str_in, capture_output=True, text=True, timeout=20).stdout.strip()
        status_str = subprocess.run(["git", "status", "--porcelain"], cwd=repo_path_str_in, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"commit_str": "", "dirty_bool": None}
    # RETURN THE STATE
    return {"commit_str": commit_str, "dirty_bool": bool(status_str) if commit_str else None}

# FUNCTION: GET THE VERSIONS OF THE RECORDED PACKAGES
def get_package_version_dict():
    """
    Returns:
        dict: package name -> version ("" when not installed)
    """
    # COLLECT EACH VERSION
    version_dict = {"python": platform.python_version()}
    for package_str in MANIFEST_PACKAGE_STR_LIST:
        try:
            version_dict[package_str] = importlib.metadata.version(package_str)
        except importlib.metadata.PackageNotFoundError:
            version_dict[package_str] = ""
    # RETURN THE VERSIONS
    return version_dict

# FUNCTION: WRITE A JSON FILE ATOMICALLY
def write_json_atomic(payload_dict_in, file_path_str_in):
    """
    Args:
        payload_dict_in (dict): Content
        file_path_str_in (str): Target path
    """
    # WRITE A TEMPORARY FILE NEXT TO THE TARGET AND REPLACE THE TARGET
    os.makedirs(os.path.dirname(file_path_str_in), exist_ok=True)
    temp_path_str = f"{file_path_str_in}.tmp"
    with open(temp_path_str, "w", encoding="utf-8", newline="\n") as file:
        json.dump(payload_dict_in, file, indent=2, default=str)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path_str, file_path_str_in)

# CLASS: A LOCK HELD BY ANOTHER RUN
class LockHeldError(RuntimeError):
    """
    Raised when the lock file of a source already exists.
    """

# CLASS: SOURCE LOCK
class SourceLock:
    """
    Exclusive lock file for one source: created with O_EXCL (atomic on local disks and SMB shares), removed on release only by
    the run that created it.
    """

    # METHOD: INITIALIZE
    def __init__(self, lock_name_str_in, run_id_str_in, lock_path_str_in=None, break_lock_bool_in=False):
        """
        Args:
            lock_name_str_in (str): Lock name (e.g. "spy_1min", "vix_family")
            run_id_str_in (str): Run id of the holder
            lock_path_str_in (str | None): Lock folder (default: config.LOCK_PATH_STR)
            break_lock_bool_in (bool): Remove an existing lock first (only when its run is known to be gone)
        """
        # STORE THE SETTINGS
        self.file_path_str = f"{lock_path_str_in or config.LOCK_PATH_STR}{lock_name_str_in}.lock"
        self.run_id_str, self.break_lock_bool, self.held_bool = run_id_str_in, break_lock_bool_in, False

    # METHOD: ACQUIRE THE LOCK
    def acquire(self):
        """
        Raises:
            LockHeldError: When another run holds the lock
        """
        # CREATE THE FOLDER
        os.makedirs(os.path.dirname(self.file_path_str), exist_ok=True)
        # REMOVE A LOCK THE USER DECLARED STALE
        if self.break_lock_bool and os.path.exists(self.file_path_str):
            print(f"⚠️ Removing the lock {self.file_path_str} (--break-lock); it was held by: {self.get_holder_str()}")
            self.mark_holder_abandoned()
            os.remove(self.file_path_str)
        # CREATE THE LOCK FILE ONLY IF IT DOES NOT EXIST
        try:
            file_descriptor_int = os.open(self.file_path_str, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockHeldError(f"Another run holds {self.file_path_str}: {self.get_holder_str()}. If that run is gone (crash, closed window), "
                                "re-run with --break-lock.") from None
        # WRITE THE HOLDER
        with os.fdopen(file_descriptor_int, "w", encoding="utf-8") as file:
            json.dump({"run_id_str": self.run_id_str, "host_str": socket.gethostname(), "pid_int": os.getpid(),
                       "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}, file)
        self.held_bool = True

    # METHOD: MARK THE MANIFEST OF A BROKEN LOCK'S HOLDER AS ABANDONED (ONLY IF IT STILL SAYS "running")
    def mark_holder_abandoned(self):
        # READ THE HOLDER'S RUN ID; ITS MANIFEST IS IN runs/ NEXT TO locks/
        try:
            with open(self.file_path_str, encoding="utf-8") as file:
                holder_run_id_str = json.load(file).get("run_id_str", "")
            manifest_path_str = f"{os.path.dirname(os.path.dirname(self.file_path_str))}/runs/{holder_run_id_str}.json"
            with open(manifest_path_str, encoding="utf-8") as file:
                manifest_dict = json.load(file)
        except (OSError, ValueError):
            return
        # RECORD WHO BROKE THE LOCK AND WHEN; THE REST OF THE MANIFEST IS KEPT
        if manifest_dict.get("status_str") == "running":
            manifest_dict.update({"status_str": "abandoned", "abandoned_by_run_id_str": self.run_id_str,
                                  "abandoned_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            write_json_atomic(manifest_dict, manifest_path_str)
            print(f"⚠️ Run {holder_run_id_str} marked abandoned in its manifest")

    # METHOD: DESCRIBE THE HOLDER OF AN EXISTING LOCK
    def get_holder_str(self):
        """
        Returns:
            str: Holder description (or the reason it cannot be read)
        """
        # READ THE LOCK FILE
        try:
            with open(self.file_path_str, encoding="utf-8") as file:
                holder_dict = json.load(file)
            return f"run {holder_dict.get('run_id_str')} on {holder_dict.get('host_str')} (pid {holder_dict.get('pid_int')}) since {holder_dict.get('started_utc')}"
        except (OSError, ValueError):
            return "unreadable lock file"

    # METHOD: RELEASE THE LOCK
    def release(self):
        # REMOVE THE FILE ONLY IF THIS RUN CREATED IT
        if self.held_bool:
            try:
                os.remove(self.file_path_str)
            except FileNotFoundError:
                pass
            self.held_bool = False

# CLASS: ONE RUN OF A SCRIPT THAT WRITES
class ScriptRun:
    """
    Context manager: lock, manifest at the start, manifest at the end (finished / failed), lock released.

        with ScriptRun("download_ibkr_ohlcv", vars(args), "spy_1min") as run:
            summary_pdf = download_session_pdf(..., run_id_str_in=run.run_id_str)
            run.summary_dict["status_count_dict"] = ...
    """

    # METHOD: INITIALIZE
    def __init__(self, script_name_str_in, argument_dict_in, lock_name_str_in=None, record_bool_in=True, break_lock_bool_in=False,
                 record_path_str_in=None):
        """
        Args:
            script_name_str_in (str): Script name
            argument_dict_in (dict): Parsed arguments (redacted before they are written)
            lock_name_str_in (str | None): Lock to hold during the run (None = no lock)
            record_bool_in (bool): False = no manifest and no lock (plans, dry runs and listings write nothing)
            break_lock_bool_in (bool): Remove a stale lock first
            record_path_str_in (str | None): Folder that holds runs/ and locks/ (default: config.INGEST_RECORD_PATH_STR)
        """
        # STORE THE SETTINGS
        self.script_name_str, self.record_bool = script_name_str_in, record_bool_in
        self.argument_dict = {key_str: redact_str(value) if isinstance(value, str) else value for key_str, value in (argument_dict_in or {}).items()}
        self.run_id_str = get_run_id_str()
        record_path_str = record_path_str_in or config.INGEST_RECORD_PATH_STR
        self.manifest_path_str = f"{record_path_str}runs/{self.run_id_str}.json"
        self.lock = SourceLock(lock_name_str_in, self.run_id_str, f"{record_path_str}locks/", break_lock_bool_in) if lock_name_str_in and record_bool_in else None
        self.summary_dict, self.manifest_dict = {}, {}
        # A BODY THAT ENDS WITHOUT AN ERROR MAY SET ANOTHER FINAL STATUS: "stopped" (A BLOCKING CHECK, NOTHING WRITTEN) OR
        # "finished_with_problems" (SOME SESSIONS OR FILES GAVE UP, FAILED OR ROLLED BACK)
        self.status_override_str = None

    # METHOD: BUILD THE MANIFEST
    def get_manifest_dict(self, status_str_in, error_str_in=""):
        """
        Args:
            status_str_in (str): "running", "finished", "finished_with_problems", "stopped" or "failed"
            error_str_in (str): Error type and message (failed runs)

        Returns:
            dict: Manifest content
        """
        # KEEP THE START FIELDS
        if not self.manifest_dict:
            self.manifest_dict = {"run_id_str": self.run_id_str, "script_str": self.script_name_str, "argument_dict": self.argument_dict,
                                  "command_str": redact_str(" ".join(sys.argv)), "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "host_str": socket.gethostname(), "target_root_str": config.TARGET_ROOT_PATH_STR, "git_dict": get_git_state_dict(),
                                  "version_dict": get_package_version_dict(), "lock_str": self.lock.file_path_str if self.lock else ""}
        # UPDATE THE END FIELDS
        self.manifest_dict.update({"status_str": status_str_in, "error_str": redact_str(error_str_in), "summary_dict": self.summary_dict,
                                   "ended_utc": datetime.now(timezone.utc).isoformat(timespec="seconds") if status_str_in != "running" else ""})
        # RETURN THE MANIFEST
        return self.manifest_dict

    # METHOD: START THE RUN
    def __enter__(self):
        # NOTHING TO RECORD FOR PLANS AND DRY RUNS
        if not self.record_bool:
            return self
        # TAKE THE LOCK, THEN WRITE THE MANIFEST
        if self.lock:
            try:
                self.lock.acquire()
            except LockHeldError as error:
                # STOP WITH THE HOLDER'S DESCRIPTION (NOTHING HAS BEEN WRITTEN)
                print(f"❌ {error}")
                raise SystemExit(1) from None
        try:
            write_json_atomic(self.get_manifest_dict("running"), self.manifest_path_str)
        except Exception:
            # A MANIFEST THAT CANNOT BE WRITTEN STOPS THE RUN (NOTHING HAS BEEN WRITTEN YET)
            if self.lock:
                self.lock.release()
            raise
        # ANY FAILURE FROM HERE UNTIL THE BODY STARTS (E.G. A CONSOLE THAT CANNOT PRINT) MARKS THE RUN FAILED AND RELEASES THE LOCK;
        # __exit__ IS NOT CALLED WHEN __enter__ RAISES
        try:
            print(f"ℹ️ Run {self.run_id_str} (manifest {self.manifest_path_str})")
        except BaseException as error:
            self.__exit__(type(error), error, None)
            raise
        return self

    # METHOD: END THE RUN
    def __exit__(self, exc_type, exc_value, traceback_in):
        # RECORD THE OUTCOME, THEN RELEASE THE LOCK (EVEN WHEN THE MANIFEST CANNOT BE WRITTEN)
        if self.record_bool:
            try:
                status_str = (self.status_override_str or "finished") if exc_type is None else "failed"
                error_str = "" if exc_type is None else f"{exc_type.__name__}: {exc_value}"
                write_json_atomic(self.get_manifest_dict(status_str, error_str), self.manifest_path_str)
            finally:
                if self.lock:
                    self.lock.release()
        # DO NOT HIDE THE ERROR
        return False
