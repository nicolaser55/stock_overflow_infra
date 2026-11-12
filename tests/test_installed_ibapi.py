import os
import sys
import inspect
import logging
import importlib

"""
Installed ibapi Check (MACHINE ONLY: run on the desktop after every TWS API or venv change; not part of run_all_tests.py or CI)

    venv-infra/Scripts/python.exe tests/test_installed_ibapi.py        (from the repository root; forward slashes work in PowerShell)

CI tests the code's logic against a simulated server with ibapi 9.81.1.post1 from PyPI as a stand-in, so it never sees the real
package. This check looks at the package actually installed in the venv:
    1. its version is at least ingest.config.MIN_IBAPI_VERSION_STR (the client refuses to connect below it);
    2. the EWrapper callbacks the client overrides still have the arguments the client expects (error, historicalData,
       historicalDataEnd, nextValidId, managedAccounts, and managedAccountsProtoBuf when the version has it), and an error in the
       installed layout is read correctly;
    3. the client drops the account list in both layouts;
    4. every ibapi logger is at WARNING or above once the client is imported (ibapi logs callbacks with their arguments at INFO).
No connection is made and no data folder is used.
"""

# POINT THE INGEST TARGET ROOT TO NOTHING USABLE (NOTHING HERE WRITES), THEN IMPORT
os.environ["SO_INGEST_TARGET_ROOT"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_no_root_")
from ingest import config
from ingest.ibkr_client import IbkrApp, get_ibapi_version_str, get_version_tuple, get_error_dict
from ibapi.wrapper import EWrapper
from ibapi.common import BarData

# FUNCTION: PRINT A PASSED CHECK
def passed(name_str_in):
    print(f"✅ {name_str_in}")

# CHECK: THE VERSION
def test_version():
    version_str = get_ibapi_version_str()
    assert get_version_tuple(version_str) >= get_version_tuple(config.MIN_IBAPI_VERSION_STR), f"ibapi {version_str} is older than {config.MIN_IBAPI_VERSION_STR}"
    passed(f"ibapi {version_str} (at least {config.MIN_IBAPI_VERSION_STR})")

# CHECK: THE CALLBACK ARGUMENTS
def test_callbacks():
    # THE ARGUMENT NAMES AFTER self, PER CALLBACK THE CLIENT OVERRIDES
    def get_name_list(method_in):
        return [name_str for name_str in inspect.signature(method_in).parameters if name_str != "self"]
    assert len(get_name_list(EWrapper.historicalData)) == 2, get_name_list(EWrapper.historicalData)
    assert len(get_name_list(EWrapper.historicalDataEnd)) == 3, get_name_list(EWrapper.historicalDataEnd)
    assert len(get_name_list(EWrapper.nextValidId)) == 1 and len(get_name_list(EWrapper.managedAccounts)) == 1
    # AN ERROR IN THE INSTALLED LAYOUT IS READ CORRECTLY (VALUES BY ARGUMENT NAME)
    sample_dict = {"errorTime": 1700000000000, "errorCode": 162, "errorString": "HMDS query returned no data", "advancedOrderRejectJson": ""}
    name_list = get_name_list(EWrapper.error)
    assert name_list[0] == "reqId" and all(name_str in sample_dict for name_str in name_list[1:]), f"unknown error() layout: {name_list}"
    error_dict = get_error_dict(tuple(sample_dict[name_str] for name_str in name_list[1:]))
    assert (error_dict["error_code_int"], error_dict["error_str"]) == (162, "HMDS query returned no data"), error_dict
    # BARS STILL CARRY THE FIELDS THE CLIENT READS
    bar = BarData()
    assert all(hasattr(bar, name_str) for name_str in ["date", "open", "high", "low", "close", "volume"])
    passed(f"callbacks: historicalData, historicalDataEnd, nextValidId, managedAccounts as expected; error() layout {name_list} read correctly")

# CHECK: THE ACCOUNT LIST IS DROPPED IN BOTH LAYOUTS
def test_account_list():
    assert "managedAccounts" in IbkrApp.__dict__, "IbkrApp no longer overrides managedAccounts"
    if hasattr(EWrapper, "managedAccountsProtoBuf"):
        assert "managedAccountsProtoBuf" in IbkrApp.__dict__, "this ibapi has managedAccountsProtoBuf, and IbkrApp does not override it"
    for name_str, method in inspect.getmembers(EWrapper, inspect.isfunction):
        if "account" in name_str.lower() and name_str.startswith("managedAccounts"):
            assert name_str in IbkrApp.__dict__, f"{name_str} is not overridden"
    passed("account list: every managedAccounts callback of this ibapi is overridden (dropped)")

# CHECK: THE IBAPI LOGGERS
def test_loggers():
    # IMPORT EVERY ibapi MODULE THAT DEFINES A LOGGER, SO ITS LOGGER EXISTS
    for module_str in ["client", "wrapper", "decoder", "reader", "connection", "comm", "utils", "orderdecoder"]:
        try:
            importlib.import_module(f"ibapi.{module_str}")
        except ImportError:
            pass
    low_list = [name_str for name_str in logging.root.manager.loggerDict if name_str.startswith("ibapi")
                and logging.getLogger(name_str).getEffectiveLevel() < logging.WARNING]
    assert not low_list, f"ibapi loggers below WARNING: {low_list}"
    passed("loggers: every ibapi logger at WARNING or above")

# RUN THE CHECKS
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_version()
    test_callbacks()
    test_account_list()
    test_loggers()
    print("\nInstalled ibapi checks passed ✅")
