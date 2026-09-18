"""Realized volatility: annualized std of daily log returns (sqrt(252))."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def log_returns(close) -> np.ndarray:
    c = np.asarray(pd.Series(close).dropna(), dtype=float)
    return np.diff(np.log(c))


def realized_vol(close, window_days: int) -> float:
    """Annualized close-to-close realized vol over the last `window_days` returns.

    `close` is a sequence/Series of daily closes in chronological order. Uses the sample std
    (ddof=1) of the last `window_days` log returns, scaled by sqrt(252). NaN if there are fewer
    than 2 returns available.
    """
    rets = log_returns(close)
    if window_days > 0:
        rets = rets[-window_days:]
    if rets.size < 2:
        return float("nan")
    return float(np.std(rets, ddof=1) * np.sqrt(TRADING_DAYS))


def realized_vol_series(close, window_days: int) -> pd.Series:
    """Rolling realized vol (same convention), indexed like `close`. Handy for the IV-vs-RV chart."""
    s = pd.Series(close, dtype=float)
    return np.log(s).diff().rolling(window_days).std(ddof=1) * np.sqrt(TRADING_DAYS)
