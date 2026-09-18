"""Serialization helpers for the Google Sheets export (no network)."""
import numpy as np
import pandas as pd

from bsm.export.sheets import align_to_header, frame_to_rows, to_sheet_frame


def test_to_sheet_frame_types():
    df = pd.DataFrame({
        "snapshot_ts": pd.to_datetime(["2026-09-18 21:30:00"]).tz_localize("UTC"),
        "expiry": pd.to_datetime(["2026-10-16"]),
        "ticker": ["AAPL"],
        "n": np.array([3], dtype=np.int64),
        "x": [np.nan],
        "y": [1.5],
        "flag": [True],
    })
    sf = to_sheet_frame(df)
    assert sf.loc[0, "snapshot_ts"] == "2026-09-18T21:30:00Z"
    assert sf.loc[0, "expiry"] == "2026-10-16"
    assert sf.loc[0, "ticker"] == "AAPL"
    assert sf.loc[0, "n"] == 3 and type(sf.loc[0, "n"]) is int
    assert sf.loc[0, "x"] is None
    assert sf.loc[0, "y"] == 1.5
    assert sf.loc[0, "flag"] is np.True_ or sf.loc[0, "flag"] is True
    rows = frame_to_rows(df)
    assert rows == [["2026-09-18T21:30:00Z", "2026-10-16", "AAPL", 3, None, 1.5, True]]


def test_align_to_header_keeps_existing_order():
    df = pd.DataFrame({"b": [1], "a": [2], "new": [3]})
    out = align_to_header(df, ["a", "b", "missing"])
    assert list(out.columns) == ["a", "b", "missing"]
    assert out.loc[0, "a"] == 2 and pd.isna(out.loc[0, "missing"])
