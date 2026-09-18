"""Yahoo Finance fetchers (yfinance). See docs/SPEC.md -> bsm/data/yahoo.py."""
import pandas as pd


def get_spot(ticker: str) -> float:
    raise NotImplementedError


def get_dividend_yield(ticker: str) -> float:
    """Continuous dividend yield q as a decimal; 0.0 if none."""
    raise NotImplementedError


def get_risk_free_rate() -> float:
    """^IRX last price / 100; fall back to config risk_free.fallback."""
    raise NotImplementedError


def get_history(ticker: str, days: int) -> pd.DataFrame:
    raise NotImplementedError


def get_option_chain(ticker: str, max_expiries: int) -> pd.DataFrame:
    """Calls and puts stacked, with added `type` ('call'/'put') and `expiry` columns."""
    raise NotImplementedError
