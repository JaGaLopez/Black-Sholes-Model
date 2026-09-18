"""Pipeline transforms on a synthetic chain (no network)."""
import numpy as np
import pandas as pd
import pytest

from bsm.model.black_scholes import price
from bsm.pipeline import IV_HISTORY_COLUMNS, OUTPUT_COLUMNS, build_contract_table, build_iv_summary

SNAP = pd.Timestamp("2026-09-18 21:30:00", tz="UTC")
SPOT, R, Q, RV = 100.0, 0.04, 0.01, 0.25


def synthetic_chain(expiries=("2026-09-18", "2026-10-16", "2026-12-18"), sigma=0.30):
    """A chain priced by BSM at `sigma` with a 2% bid/ask spread; strikes 60..140."""
    rows = []
    for exp in expiries:
        dte = (pd.Timestamp(exp) - pd.Timestamp("2026-09-18")).days
        T = max(dte, 0) / 365
        for k in np.arange(60, 141, 5.0):
            for side in ("call", "put"):
                p = float(price(SPOT, k, T, R, Q, sigma, side))
                rows.append({
                    "contractSymbol": f"SYN{exp.replace('-', '')}{side[0].upper()}{int(k * 1000):08d}",
                    "lastTradeDate": SNAP, "strike": k, "lastPrice": p,
                    "bid": p * 0.99, "ask": p * 1.01, "change": 0.0, "percentChange": 0.0,
                    "volume": 10.0, "openInterest": 50 if k != 65 else 1,  # 65 strike is illiquid
                    "impliedVolatility": sigma + 0.01, "inTheMoney": (k < SPOT) == (side == "call"),
                    "contractSize": "REGULAR", "currency": "USD", "type": side, "expiry": pd.Timestamp(exp),
                })
    return pd.DataFrame(rows)


def test_schema_and_filters():
    chain = synthetic_chain()
    df = build_contract_table(chain, ticker="SYN", spot=SPOT, r=R, q=Q, rv=RV, snapshot_ts=SNAP,
                              min_days_to_expiry=1, moneyness_band=0.30, min_open_interest=10)
    assert list(df.columns) == OUTPUT_COLUMNS
    assert (df["days_to_expiry"] >= 1).all()  # 0DTE dropped
    assert df["expiry"].nunique() == 2
    assert ((df["moneyness"] - 1).abs() <= 0.30).all()  # 60/65/135/140 dropped
    assert 65.0 not in set(df["strike"])  # low OI dropped
    assert (df["bid"] > 0).all()
    assert (df["ticker"] == "SYN").all()
    assert set(df["type"]) == {"call", "put"}


def test_model_columns_are_consistent():
    df = build_contract_table(synthetic_chain(), ticker="SYN", spot=SPOT, r=R, q=Q, rv=RV, snapshot_ts=SNAP)
    # Mid was generated at sigma=0.30, so iv_model should recover it wherever it is solvable.
    ok = np.isfinite(df["iv_model"])
    assert ok.mean() > 0.9
    np.testing.assert_allclose(df.loc[ok, "iv_model"], 0.30, atol=1e-6)
    # bs_price_realized uses the realized vol, mispricing = mid - that.
    expected = price(SPOT, df["strike"], df["T_years"], R, Q, RV, df["type"])
    np.testing.assert_allclose(df["bs_price_realized"], expected)
    np.testing.assert_allclose(df["mispricing"], df["mid"] - expected)
    np.testing.assert_allclose(df["mispricing_pct"], df["mispricing"] / df["mid"])
    np.testing.assert_allclose(df["time_value"], df["mid"] - df["intrinsic"])
    np.testing.assert_allclose(df["spread_pct"], (df["ask"] - df["bid"]) / df["mid"])
    np.testing.assert_allclose(df["T_years"], df["days_to_expiry"] / 365)
    assert np.isfinite(df[["delta", "gamma", "vega", "theta", "rho"]]).all().all()
    calls = df[df["type"] == "call"]
    assert ((calls["delta"] >= 0) & (calls["delta"] <= 1)).all()
    puts = df[df["type"] == "put"]
    assert ((puts["delta"] <= 0) & (puts["delta"] >= -1)).all()
    assert (df["gamma"] >= 0).all() and (df["vega"] >= 0).all()


def test_iv_summary():
    df = build_contract_table(synthetic_chain(), ticker="SYN", spot=SPOT, r=R, q=Q, rv=RV, snapshot_ts=SNAP)
    summary = build_iv_summary(df)
    assert list(summary.columns) == IV_HISTORY_COLUMNS
    assert len(summary) == 2  # one row per expiry
    np.testing.assert_allclose(summary["atm_iv"], 0.30, atol=1e-6)
    np.testing.assert_allclose(summary["iv_call_25d"], 0.30, atol=1e-6)
    np.testing.assert_allclose(summary["iv_put_25d"], 0.30, atol=1e-6)
    np.testing.assert_allclose(summary["skew_25d"], 0.0, atol=1e-6)
    np.testing.assert_allclose(summary["vol_risk_premium"], 0.30 - RV, atol=1e-6)
    assert (summary["ticker"] == "SYN").all()
    assert (summary["n_contracts"] > 0).all()


def test_empty_chain():
    empty = build_contract_table(synthetic_chain().iloc[0:0], ticker="X", spot=1, r=0, q=0, rv=0.2, snapshot_ts=SNAP)
    assert empty.empty and list(empty.columns) == OUTPUT_COLUMNS
    assert build_iv_summary(empty).empty
