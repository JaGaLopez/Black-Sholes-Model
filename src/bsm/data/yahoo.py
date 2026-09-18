"""Yahoo Finance fetchers (yfinance). See docs/SPEC.md -> bsm/data/yahoo.py.

Every public function:
- retries with exponential backoff (Yahoo rate-limits and returns transient 429s / empty frames), and
- memoizes its result for the life of the process, so one pipeline run never refetches the same thing.
  Call `clear_cache()` to force fresh data (the scheduler does this between runs).

Yahoo is not an official API: quotes are ~15 min delayed and the schema changes occasionally.
"""
from __future__ import annotations

import datetime as dt
import functools
import logging
import math
import random
import time
from typing import Callable, TypeVar

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

RISK_FREE_TICKER = "^IRX"  # 13-week T-bill yield, quoted in percent
DEFAULT_RISK_FREE = 0.04

# Columns we rely on from Ticker.option_chain(); verified against yfinance 1.7 (2026-09-18).
CHAIN_COLUMNS = [
    "contractSymbol", "lastTradeDate", "strike", "lastPrice", "bid", "ask", "change",
    "percentChange", "volume", "openInterest", "impliedVolatility", "inTheMoney",
    "contractSize", "currency",
]

T = TypeVar("T")


# --------------------------------------------------------------------------------------------
# Retry + cache plumbing
# --------------------------------------------------------------------------------------------

class YahooFetchError(RuntimeError):
    """Raised when a fetch keeps failing after all retries (or returns nothing usable)."""


def with_retry(attempts: int = 4, base_delay: float = 2.0, max_delay: float = 30.0):
    """Retry a fetch on any exception with exponential backoff and jitter."""

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - yfinance raises a grab-bag of types
                    last_exc = exc
                    if i == attempts - 1:
                        break
                    delay = min(max_delay, base_delay * 2**i) * (1 + 0.25 * random.random())
                    log.warning("%s(%s) failed (%s: %s); retry %d/%d in %.1fs",
                                fn.__name__, args[:1], type(exc).__name__, exc, i + 1, attempts - 1, delay)
                    time.sleep(delay)
            raise YahooFetchError(f"{fn.__name__}{args[:1]} failed after {attempts} attempts: {last_exc}") from last_exc

        return wrapper

    return deco


_CACHED: list = []


def cached(fn):
    """functools.cache, but registered so clear_cache() can reset every fetcher at once."""
    wrapped = functools.cache(fn)
    _CACHED.append(wrapped)
    return wrapped


def clear_cache() -> None:
    for fn in _CACHED:
        fn.cache_clear()


@cached
def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol)


# --------------------------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------------------------

@cached
@with_retry()
def get_spot(ticker: str) -> float:
    """Last traded price from Ticker.fast_info["lastPrice"]."""
    spot = _ticker(ticker).fast_info["lastPrice"]
    if spot is None or not math.isfinite(float(spot)) or float(spot) <= 0:
        raise ValueError(f"no usable spot for {ticker}: {spot!r}")
    return float(spot)


@cached
@with_retry()
def get_dividend_yield(ticker: str) -> float:
    """Continuous dividend yield q as a decimal; 0.0 if none.

    Computed from the trailing 12 months of cash dividends divided by spot, then converted to a
    continuous rate: q = ln(1 + D/S). (Ticker.info's 'dividendYield' is avoided: it is slow,
    rate-limited, and has flipped between percent and decimal across yfinance versions.)
    """
    divs = _ticker(ticker).dividends
    if divs is None or len(divs) == 0:
        return 0.0
    idx = pd.DatetimeIndex(divs.index)
    cutoff = pd.Timestamp.now(tz=idx.tz) - pd.Timedelta(days=365)
    trailing = float(divs[idx > cutoff].sum())
    if trailing <= 0:
        return 0.0
    return float(math.log1p(trailing / get_spot(ticker)))


@cached
@with_retry()
def _irx_last() -> float:
    last = _ticker(RISK_FREE_TICKER).fast_info["lastPrice"]
    if last is None or not math.isfinite(float(last)):
        raise ValueError(f"no usable {RISK_FREE_TICKER} quote: {last!r}")
    return float(last)


def get_risk_free_rate(fallback: float = DEFAULT_RISK_FREE) -> float:
    """^IRX last price / 100; fall back to config risk_free.fallback."""
    try:
        r = _irx_last() / 100.0
    except YahooFetchError as exc:
        log.warning("risk-free fetch failed (%s); using fallback %.4f", exc, fallback)
        return float(fallback)
    if not (0.0 <= r <= 0.25):  # a sanity band; ^IRX is a percent, so 4.0 -> 0.04
        log.warning("risk-free %.4f outside sanity band; using fallback %.4f", r, fallback)
        return float(fallback)
    return r


@cached
@with_retry()
def get_history(ticker: str, days: int) -> pd.DataFrame:
    """Daily OHLCV for the last `days` trading days (columns: Open, High, Low, Close, Volume, ...).

    Index is a tz-naive DatetimeIndex of session dates, ascending. Fetches a little extra so the
    frame has at least `days` rows even across holidays.
    """
    calendar_days = int(days * 1.6) + 10
    start = (dt.date.today() - dt.timedelta(days=calendar_days)).isoformat()
    hist = _ticker(ticker).history(start=start, interval="1d", auto_adjust=False, actions=True)
    if hist is None or hist.empty:
        raise ValueError(f"empty price history for {ticker}")
    hist = hist.copy()
    hist.index = pd.DatetimeIndex(hist.index).tz_localize(None).normalize()
    hist.index.name = "date"
    return hist.tail(days)


def _one_expiry(tk: yf.Ticker, expiry: str) -> pd.DataFrame:
    chain = tk.option_chain(expiry)
    frames = []
    for side, df in (("call", chain.calls), ("put", chain.puts)):
        if df is None or df.empty:
            continue
        df = df.copy()
        df["type"] = side
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=CHAIN_COLUMNS + ["type", "expiry"])
    out = pd.concat(frames, ignore_index=True)
    out["expiry"] = pd.Timestamp(expiry)
    return out


@cached
@with_retry()
def get_option_chain(ticker: str, max_expiries: int, min_days_to_expiry: int = 1) -> pd.DataFrame:
    """Calls and puts stacked, with added `type` ('call'/'put') and `expiry` columns.

    Takes the nearest `max_expiries` expiries that are at least `min_days_to_expiry` calendar days
    away (so 0DTE contracts aren't wasted on one of the slots). Missing columns are added as NaN
    so downstream code can rely on CHAIN_COLUMNS.
    """
    tk = _ticker(ticker)
    expiries = list(tk.options)
    if not expiries:
        raise ValueError(f"no option expiries listed for {ticker}")
    today = dt.date.today()
    eligible = [e for e in expiries if (dt.date.fromisoformat(e) - today).days >= min_days_to_expiry]
    chosen = eligible[:max_expiries]
    frames = []
    for exp in chosen:
        try:
            frames.append(_one_expiry(tk, exp))
        except Exception as exc:  # noqa: BLE001 - one bad expiry shouldn't kill the ticker
            log.warning("%s %s chain failed: %s", ticker, exp, exc)
            raise
    if not frames:
        raise ValueError(f"no option chains fetched for {ticker}")
    out = pd.concat(frames, ignore_index=True)
    for col in CHAIN_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out.insert(0, "ticker", ticker)
    return out
