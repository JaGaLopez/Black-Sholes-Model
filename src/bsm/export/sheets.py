"""Push pipeline output to Google Sheets (Tableau Public's data source) + local CSV mirror.

See docs/SPEC.md -> bsm/export/sheets.py for tab layout and size limits.
"""


def write_options_latest(df, spreadsheet_name: str):
    """Clear and overwrite the `options_latest` tab."""
    raise NotImplementedError


def append_iv_history(summary_df, spreadsheet_name: str):
    """Append one row per ticker/expiry (ATM IV, 25-delta skew, realized vol) for this run."""
    raise NotImplementedError


def append_run_log(status: dict, spreadsheet_name: str):
    raise NotImplementedError
