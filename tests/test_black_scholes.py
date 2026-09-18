"""Model tests: put-call parity, textbook values, Greeks vs finite differences, IV round-trip."""
import numpy as np
import pytest

from bsm.model.black_scholes import greeks, intrinsic, price
from bsm.model.implied_vol import implied_vol
from bsm.model.volatility import realized_vol, realized_vol_series

# A grid of inputs that covers ITM/ATM/OTM, short/long dated, with and without dividends.
S = np.array([100.0, 100.0, 100.0, 50.0, 250.0, 100.0])
K = np.array([80.0, 100.0, 120.0, 55.0, 200.0, 100.0])
T = np.array([0.05, 0.5, 1.0, 2.0, 0.25, 0.01])
R = np.array([0.04, 0.05, 0.03, 0.01, 0.045, 0.04])
Q = np.array([0.0, 0.02, 0.01, 0.0, 0.015, 0.0])
SIG = np.array([0.2, 0.25, 0.3, 0.6, 0.18, 0.4])


# --- Textbook values (Hull, Options, Futures and Other Derivatives) --------------------------


def test_hull_example_prices():
    # Hull Ex. 15.6: S=42, K=40, r=10%, sigma=20%, T=0.5 -> C=4.76, P=0.81
    c = price(42, 40, 0.5, 0.10, 0.0, 0.20, "call")
    p = price(42, 40, 0.5, 0.10, 0.0, 0.20, "put")
    assert c == pytest.approx(4.7594, abs=5e-4)
    assert p == pytest.approx(0.8086, abs=5e-4)


def test_hull_example_greeks():
    # Hull Ch. 19: S=49, K=50, r=5%, sigma=20%, T=20 weeks (0.3846)
    g = greeks(49, 50, 0.3846, 0.05, 0.0, 0.20, "call")
    assert g["delta"] == pytest.approx(0.522, abs=1e-3)
    assert g["gamma"] == pytest.approx(0.066, abs=1e-3)
    assert g["vega"] * 100 == pytest.approx(12.1, abs=0.05)  # Hull quotes per 1.0 vol
    assert g["theta"] * 365 == pytest.approx(-4.31, abs=0.01)  # Hull quotes per year
    assert g["rho"] * 100 == pytest.approx(8.91, abs=0.01)  # Hull quotes per 1.0 rate


# --- Parity and bounds ----------------------------------------------------------------------


def test_put_call_parity():
    c = price(S, K, T, R, Q, SIG, "call")
    p = price(S, K, T, R, Q, SIG, "put")
    lhs = c - p
    rhs = S * np.exp(-Q * T) - K * np.exp(-R * T)
    np.testing.assert_allclose(lhs, rhs, atol=1e-8)


def test_parity_greeks():
    gc = greeks(S, K, T, R, Q, SIG, "call")
    gp = greeks(S, K, T, R, Q, SIG, "put")
    np.testing.assert_allclose(gc["delta"] - gp["delta"], np.exp(-Q * T), atol=1e-10)
    np.testing.assert_allclose(gc["gamma"], gp["gamma"], atol=1e-12)
    np.testing.assert_allclose(gc["vega"], gp["vega"], atol=1e-12)
    np.testing.assert_allclose(gc["rho"] - gp["rho"], K * T * np.exp(-R * T) / 100, atol=1e-10)


def test_price_bounds_and_intrinsic():
    c = price(S, K, T, R, Q, SIG, "call")
    p = price(S, K, T, R, Q, SIG, "put")
    assert np.all(c >= np.maximum(S * np.exp(-Q * T) - K * np.exp(-R * T), 0) - 1e-12)
    assert np.all(c <= S * np.exp(-Q * T) + 1e-12)
    assert np.all(p >= np.maximum(K * np.exp(-R * T) - S * np.exp(-Q * T), 0) - 1e-12)
    assert np.all(p <= K * np.exp(-R * T) + 1e-12)
    np.testing.assert_allclose(intrinsic(S, K, "call"), np.maximum(S - K, 0))
    np.testing.assert_allclose(intrinsic(S, K, "put"), np.maximum(K - S, 0))


def test_mixed_option_type_array():
    types = np.array(["call", "put", "call", "put", "call", "put"])
    mixed = price(S, K, T, R, Q, SIG, types)
    calls = price(S, K, T, R, Q, SIG, "call")
    puts = price(S, K, T, R, Q, SIG, "put")
    np.testing.assert_allclose(mixed, np.where(types == "call", calls, puts))


def test_degenerate_inputs_do_not_raise():
    c = price(np.array([100.0, 100.0]), 90.0, np.array([0.0, 1.0]), 0.05, 0.0, np.array([0.2, 0.0]), "call")
    assert c[0] == pytest.approx(10.0)  # expired: intrinsic
    assert c[1] == pytest.approx(100 - 90 * np.exp(-0.05))  # zero vol: discounted forward intrinsic
    g = greeks(100.0, 90.0, 0.0, 0.05, 0.0, 0.2, "call")
    assert g["delta"] == 1.0 and g["gamma"] == 0.0 and np.isfinite(g["theta"])


# --- Greeks vs finite differences -----------------------------------------------------------


@pytest.mark.parametrize("otype", ["call", "put"])
def test_greeks_match_finite_differences(otype):
    g = greeks(S, K, T, R, Q, SIG, otype)
    h = 1e-4
    delta_fd = (price(S + h, K, T, R, Q, SIG, otype) - price(S - h, K, T, R, Q, SIG, otype)) / (2 * h)
    gamma_fd = (price(S + h, K, T, R, Q, SIG, otype) - 2 * price(S, K, T, R, Q, SIG, otype)
                + price(S - h, K, T, R, Q, SIG, otype)) / h**2
    vega_fd = (price(S, K, T, R, Q, SIG + h, otype) - price(S, K, T, R, Q, SIG - h, otype)) / (2 * h) / 100
    theta_fd = (price(S, K, T - h, R, Q, SIG, otype) - price(S, K, T + h, R, Q, SIG, otype)) / (2 * h) / 365
    rho_fd = (price(S, K, T, R + h, Q, SIG, otype) - price(S, K, T, R - h, Q, SIG, otype)) / (2 * h) / 100
    np.testing.assert_allclose(g["delta"], delta_fd, rtol=1e-5, atol=1e-7)
    np.testing.assert_allclose(g["gamma"], gamma_fd, rtol=1e-3, atol=1e-5)
    np.testing.assert_allclose(g["vega"], vega_fd, rtol=1e-5, atol=1e-7)
    np.testing.assert_allclose(g["theta"], theta_fd, rtol=1e-4, atol=1e-7)
    np.testing.assert_allclose(g["rho"], rho_fd, rtol=1e-5, atol=1e-7)


# --- Implied vol ----------------------------------------------------------------------------


@pytest.mark.parametrize("otype", ["call", "put"])
def test_iv_round_trip(otype):
    p = price(S, K, T, R, Q, SIG, otype)
    iv = implied_vol(p, S, K, T, R, Q, otype)
    np.testing.assert_allclose(iv, SIG, rtol=1e-6, atol=1e-7)


def test_iv_round_trip_extremes():
    # Deep OTM / very high and low vols exercise the Brent fallback.
    s, k, t, r, q = 100.0, np.array([60.0, 160.0, 100.0, 100.0]), 0.1, 0.03, 0.0
    sig = np.array([0.9, 1.5, 0.02, 3.0])
    for otype in ("call", "put"):
        p = price(s, k, t, r, q, sig, otype)
        iv = implied_vol(p, s, k, t, r, q, otype)
        np.testing.assert_allclose(iv, sig, rtol=1e-5)


def test_iv_scalar_and_mixed_types():
    iv = implied_vol(4.7594, 42, 40, 0.5, 0.10, 0.0, "call")
    assert float(iv) == pytest.approx(0.20, abs=1e-4)
    types = np.array(["call", "put"])
    p = price(100.0, 100.0, 0.5, 0.05, 0.0, 0.3, types)
    np.testing.assert_allclose(implied_vol(p, 100.0, 100.0, 0.5, 0.05, 0.0, types), 0.3, rtol=1e-6)


def test_iv_nan_when_no_solution():
    # Below intrinsic, above the upper bound, non-positive price, expired.
    mp = np.array([5.0, 150.0, 0.0, 3.0, np.nan])
    s = 100.0
    k = np.array([90.0, 100.0, 100.0, 100.0, 100.0])
    t = np.array([0.5, 0.5, 0.5, 0.0, 0.5])
    iv = implied_vol(mp, s, k, t, 0.05, 0.0, "call")
    assert np.all(np.isnan(iv))


# --- Realized vol ---------------------------------------------------------------------------


def test_realized_vol_matches_manual_calc():
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.02, 200)
    close = 100 * np.exp(np.cumsum(rets))
    rv = realized_vol(close, 30)
    manual = np.std(np.diff(np.log(close))[-30:], ddof=1) * np.sqrt(252)
    assert rv == pytest.approx(manual)
    # Roughly 2% daily -> ~32% annualized on the full window
    assert realized_vol(close, 0) == pytest.approx(0.02 * np.sqrt(252), rel=0.2)
    series = realized_vol_series(close, 30)
    assert series.iloc[-1] == pytest.approx(rv)
    assert np.isnan(realized_vol([100.0, 101.0], 30))
