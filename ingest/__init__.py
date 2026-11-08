"""
Stock Overflow Ingest (package of stock_overflow_infra)

    ingest.config           every constant: target root (the quarantine), folder names, IBKR connection, contract, request and pacing settings
    ingest.records          run manifests, source locks, checksums, secret redaction
    ingest.ibkr_client      IBKR connection: per-request results, version-agnostic callbacks, account list dropped
    ingest.ibkr_download    SPY: session selection, concurrent download, minute-by-minute check, staging files, download log
    ingest.sessions         New York clock, NYSE schedule, session checks, issue report
    ingest.raw_files        file names, text-preserving read / write, roll-up rules, merge engine
    ingest.raw_maintenance  SPY: merge staging into raw, organize, clean orphans, rebuild, issue report
    ingest.index_config     VIX / VIX3M settings and folders
    ingest.index_download   VIX / VIX3M download (daily and 1-minute)
    ingest.index_merge      VIX / VIX3M merge staging into raw
    ingest.index_status     VIX / VIX3M folder status
    ingest.fred             FRED / ALFRED series with first-release and revision rows (DTB3): fetch, check, append-only publish

This package does not import the research workspace (package so). The interface between the two is the file layout described
in docs/DATA_CONTRACT.md.
"""

import sys as _sys

# PRINT IN UTF-8 EVEN WHEN THE OUTPUT IS A PIPE OR A FILE ON WINDOWS (THE DEFAULT THERE IS CP1252, WHICH CANNOT PRINT THE STATUS
# SYMBOLS AND WOULD STOP A RUN AFTER IT HAS STARTED)
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
