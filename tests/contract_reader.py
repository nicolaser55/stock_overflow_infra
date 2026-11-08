import os
import pandas as pd

"""
Contract Reader (tests only)

A copy of how stock_overflow_workspace reads the SPY raw folder (so.core.raw_data._read_raw_ohlcv_pdf, read on 2026-11-08 at
workspace commit ed489c1), written out here so the tests of this repository do not import the research package:

    - every *.csv file directly in the folder is read (no sub-folders, whatever the layout: year, month or day files);
    - the frames are concatenated, repeated column names dropped;
    - "timestamp" is parsed as UTC and converted to New York time; "date" is the New York date of the timestamp.

If the research reader changes, docs/DATA_CONTRACT.md and this file change together.
"""

# FUNCTION: READ A RAW FOLDER THE WAY THE RESEARCH DOES
def read_raw_folder_like_research_pdf(raw_path_str_in):
    """
    Args:
        raw_path_str_in (str): Raw folder ending with "/"

    Returns:
        pd.DataFrame: Every row of every CSV, timestamp in New York time, date
    """
    # COLLECT THE CSV FILES DIRECTLY IN THE FOLDER
    file_path_list = sorted(f"{raw_path_str_in}{entry.name}" for entry in os.scandir(raw_path_str_in) if entry.is_file() and entry.name.lower().endswith(".csv"))
    # READ AND CONCATENATE THEM
    raw_pdf = pd.concat([pd.read_csv(file_path_str) for file_path_str in file_path_list], axis=0).reset_index(drop=True)
    raw_pdf = raw_pdf.loc[:, ~raw_pdf.columns.duplicated()].reset_index(drop=True)
    # CONVERT THE TIMESTAMPS AND ADD THE SESSION DATE
    raw_pdf["timestamp"] = pd.to_datetime(raw_pdf["timestamp"], utc=True).dt.tz_convert("America/New_York")
    raw_pdf["date"] = raw_pdf["timestamp"].dt.date
    # RETURN THE FRAME
    return raw_pdf
