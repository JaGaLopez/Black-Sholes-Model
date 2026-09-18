"""Push pipeline output to Google Sheets (Tableau Public's data source) + local CSV mirror.

See docs/SPEC.md -> bsm/export/sheets.py for tab layout and size limits.

Auth: a service account. `GOOGLE_SERVICE_ACCOUNT_FILE` points at the JSON key (local runs);
`GOOGLE_SERVICE_ACCOUNT_JSON` may hold the JSON itself (CI). The spreadsheet must already exist
and be shared with the service account's email as Editor (docs/GOOGLE_SETUP.md).

Rules implemented here:
- options_latest is replaced in ONE batch write (set_with_dataframe) and never with an empty frame.
- iv_history / run_log are appended with append_rows, one API call per run.
- Dates go out as ISO strings, numbers as numbers, NaN as blank cells, so Tableau infers types.
- Existing headers win: if a tab already has a header row, the frame is aligned to it (missing
  columns blank, extra columns dropped with a warning), so a schema drift never silently
  breaks the Tableau data source.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path

import gspread
import numpy as np
import pandas as pd
from gspread_dataframe import set_with_dataframe

log = logging.getLogger(__name__)

TAB_OPTIONS = "options_latest"
TAB_IV_HISTORY = "iv_history"
TAB_RUN_LOG = "run_log"
DEFAULT_TAB_SHEET1 = "Sheet1"  # a fresh spreadsheet's placeholder tab; removed once ours exist

RUN_LOG_COLUMNS = [
    "snapshot_ts", "status", "rows_written", "tickers_ok", "tickers_failed", "errors",
    "duration_s", "risk_free", "exported",
]

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# --------------------------------------------------------------------------------------------
# Auth / spreadsheet access
# --------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_client() -> gspread.Client:
    """gspread client from GOOGLE_SERVICE_ACCOUNT_FILE (path) or GOOGLE_SERVICE_ACCOUNT_JSON (contents)."""
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if path and Path(path).is_file():
        return gspread.service_account(filename=path, scopes=_SCOPES)
    if raw:
        return gspread.service_account_from_dict(json.loads(raw), scopes=_SCOPES)
    raise RuntimeError(
        "No Google credentials: set GOOGLE_SERVICE_ACCOUNT_FILE to the service-account JSON path "
        f"(got {path!r}) or GOOGLE_SERVICE_ACCOUNT_JSON to its contents. See docs/GOOGLE_SETUP.md."
    )


def open_spreadsheet(name: str) -> gspread.Spreadsheet:
    return get_client().open(name)


def get_or_create_worksheet(sh: gspread.Spreadsheet, title: str, rows: int = 100, cols: int = 40) -> gspread.Worksheet:
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        log.info("creating tab %r in %r", title, sh.title)
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


# --------------------------------------------------------------------------------------------
# Serialization helpers (pure; unit-tested)
# --------------------------------------------------------------------------------------------

def to_sheet_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of `df` with datetimes as ISO strings, bools as bools, NaN/inf as None, numpy scalars as Python."""
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            ts = pd.to_datetime(s)
            if getattr(ts.dt, "tz", None) is not None:
                ts = ts.dt.tz_convert("UTC")
                out[col] = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ").where(ts.notna(), None)
            else:
                all_midnight = bool(((ts.dt.hour == 0) & (ts.dt.minute == 0) & (ts.dt.second == 0)).all())
                fmt = "%Y-%m-%d" if all_midnight else "%Y-%m-%dT%H:%M:%S"
                out[col] = ts.dt.strftime(fmt).where(ts.notna(), None)
        elif pd.api.types.is_bool_dtype(s):
            out[col] = s.astype(bool)
        elif pd.api.types.is_numeric_dtype(s):
            vals = s.astype(float).to_numpy()
            obj = s.astype(object).where(np.isfinite(vals), None)
            out[col] = obj
        else:
            out[col] = s.astype(object).apply(_scalar)
    return out


def _scalar(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        v = v if not isinstance(v, pd.Timestamp) or v.tz is None else v.tz_convert("UTC")
        return v.strftime("%Y-%m-%dT%H:%M:%SZ" if getattr(v, "tzinfo", None) else "%Y-%m-%dT%H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def align_to_header(df: pd.DataFrame, header: list[str]) -> pd.DataFrame:
    """Reorder/limit `df` to an existing sheet header; missing columns become blank."""
    header = [h for h in header if h]
    extra = [c for c in df.columns if c not in header]
    missing = [h for h in header if h not in df.columns]
    if extra:
        log.warning("dropping columns not in existing sheet header: %s", extra)
    if missing:
        log.warning("sheet header has columns the frame lacks (left blank): %s", missing)
    return df.reindex(columns=header)


def frame_to_rows(df: pd.DataFrame) -> list[list]:
    sf = to_sheet_frame(df)
    return [[None if (isinstance(v, float) and math.isnan(v)) else v for v in row] for row in sf.itertuples(index=False, name=None)]


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------

def write_options_latest(df: pd.DataFrame, spreadsheet_name: str) -> int:
    """Clear and overwrite the `options_latest` tab. Refuses an empty frame. Returns rows written."""
    if df is None or df.empty:
        raise ValueError("refusing to overwrite options_latest with an empty frame")
    sh = open_spreadsheet(spreadsheet_name)
    ws = get_or_create_worksheet(sh, TAB_OPTIONS, rows=len(df) + 1, cols=len(df.columns))
    existing = ws.row_values(1)
    frame = align_to_header(df, existing) if existing else df
    sheet_frame = to_sheet_frame(frame)
    ws.clear()
    # One batch write; resize=True shrinks the grid to the data so the 10M-cell budget isn't wasted.
    set_with_dataframe(ws, sheet_frame, include_index=False, include_column_header=True, resize=True)
    _drop_placeholder_tab(sh)
    log.info("%s/%s: wrote %d rows x %d cols", spreadsheet_name, TAB_OPTIONS, len(sheet_frame), len(sheet_frame.columns))
    return len(sheet_frame)


def _append_frame(
    df: pd.DataFrame, spreadsheet_name: str, tab: str, columns: list[str], dedupe_on: list[str] | None = None
) -> int:
    """Append `df` to `tab`, creating it (with header) if needed.

    With `dedupe_on`, rows whose key already exists in the tab are skipped, so re-running the
    pipeline on the same day doesn't double up the history.
    """
    if df is None or df.empty:
        log.warning("%s: nothing to append", tab)
        return 0
    sh = open_spreadsheet(spreadsheet_name)
    ws = get_or_create_worksheet(sh, tab, rows=1000, cols=len(columns))
    existing = ws.row_values(1)
    if not existing:
        ws.update(range_name="A1", values=[columns], value_input_option="USER_ENTERED")
        existing = columns
    frame = align_to_header(df, existing)
    if dedupe_on and all(k in existing for k in dedupe_on):
        current = pd.DataFrame(ws.get_all_records(expected_headers=existing, numericise_ignore=["all"]))
        if not current.empty:
            keyframe = to_sheet_frame(frame[dedupe_on]).astype(str)
            have = set(map(tuple, current[dedupe_on].astype(str).to_numpy()))
            mask = ~keyframe.apply(tuple, axis=1).isin(have)
            skipped = int((~mask).sum())
            if skipped:
                log.warning("%s: skipping %d rows already present for %s", tab, skipped, dedupe_on)
            frame = frame.loc[mask]
            if frame.empty:
                return 0
    rows = frame_to_rows(frame)
    ws.append_rows(rows, value_input_option="USER_ENTERED", insert_data_option="INSERT_ROWS", table_range="A1")
    _drop_placeholder_tab(sh)
    log.info("%s/%s: appended %d rows", spreadsheet_name, tab, len(rows))
    return len(rows)


def append_iv_history(summary_df: pd.DataFrame, spreadsheet_name: str) -> int:
    """Append one row per ticker/expiry (ATM IV, 25-delta skew, realized vol) for this run."""
    from bsm.pipeline import IV_HISTORY_COLUMNS  # local import to avoid a cycle at module load

    return _append_frame(
        summary_df, spreadsheet_name, TAB_IV_HISTORY, IV_HISTORY_COLUMNS, dedupe_on=["date", "ticker", "expiry"]
    )


def append_run_log(status: dict, spreadsheet_name: str) -> int:
    row = {k: status.get(k) for k in RUN_LOG_COLUMNS}
    row["row_counts"] = json.dumps(status.get("row_counts", {}))
    df = pd.DataFrame([row], columns=RUN_LOG_COLUMNS + ["row_counts"])
    return _append_frame(df, spreadsheet_name, TAB_RUN_LOG, RUN_LOG_COLUMNS + ["row_counts"])


def _drop_placeholder_tab(sh: gspread.Spreadsheet) -> None:
    """Delete the default empty 'Sheet1' once at least one of our tabs exists (keeps the source clean)."""
    titles = [w.title for w in sh.worksheets()]
    if DEFAULT_TAB_SHEET1 in titles and len(titles) > 1:
        ws = sh.worksheet(DEFAULT_TAB_SHEET1)
        if not any(any(cell for cell in row) for row in ws.get_all_values()):
            try:
                sh.del_worksheet(ws)
            except gspread.exceptions.APIError as exc:
                log.debug("could not delete %s: %s", DEFAULT_TAB_SHEET1, exc)


# --------------------------------------------------------------------------------------------
# Read-back (for verification scripts / tests)
# --------------------------------------------------------------------------------------------

def read_tab(spreadsheet_name: str, tab: str) -> pd.DataFrame:
    ws = open_spreadsheet(spreadsheet_name).worksheet(tab)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])
