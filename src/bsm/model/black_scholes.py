"""Vectorized Black-Scholes-Merton pricing and Greeks. See docs/SPEC.md for formulas and units.

Units (project convention, see CLAUDE.md):
- r, q, sigma are decimals (0.04, not 4). T is in years.
- theta is per calendar day, vega per 1 vol point (0.01), rho per 1% rate move (0.01).

All functions broadcast over numpy arrays. `option_type` is 'call'/'put' (scalar or array).
Contracts with T <= 0 or sigma <= 0 are priced at intrinsic value with degenerate Greeks
(delta = indicator, everything else 0) rather than raising.
"""
from __future__ import annotations

import numpy as np
from scipy.special import ndtr  # standard normal CDF, vectorized

_SQRT_2PI = np.sqrt(2.0 * np.pi)


def _npdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / _SQRT_2PI


def _is_call(option_type) -> np.ndarray:
    """Boolean array: True for calls. Accepts 'call'/'put', 'c'/'p', or an existing bool array."""
    arr = np.asarray(option_type)
    if arr.dtype == bool:
        return arr
    flat = np.char.lower(arr.astype(str))
    return np.char.startswith(flat, "c")


def _prep(S, K, T, r, q, sigma, option_type):
    S, K, T, r, q, sigma, is_call = np.broadcast_arrays(
        *[np.asarray(x, dtype=float) for x in (S, K, T, r, q, sigma)], _is_call(option_type)
    )
    return S, K, T, r, q, sigma, is_call


def d1_d2(S, K, T, r, q, sigma):
    """d1 and d2 of BSM. Returns (d1, d2); NaN/inf where T or sigma is zero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def intrinsic(S, K, option_type) -> np.ndarray:
    S, K, is_call = np.broadcast_arrays(
        np.asarray(S, dtype=float), np.asarray(K, dtype=float), _is_call(option_type)
    )
    return np.where(is_call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))


def price(S, K, T, r, q, sigma, option_type) -> np.ndarray:
    """BSM price. Call = S e^{-qT} N(d1) - K e^{-rT} N(d2); Put = K e^{-rT} N(-d2) - S e^{-qT} N(-d1)."""
    S, K, T, r, q, sigma, is_call = _prep(S, K, T, r, q, sigma, option_type)
    degenerate = (T <= 0) | (sigma <= 0)

    d1, d2 = d1_d2(S, K, T, r, q, sigma)
    disc_q = S * np.exp(-q * T)
    disc_r = K * np.exp(-r * T)
    call = disc_q * ndtr(d1) - disc_r * ndtr(d2)
    put = disc_r * ndtr(-d2) - disc_q * ndtr(-d1)
    out = np.where(is_call, call, put)

    # Zero-vol / expired contracts: discounted intrinsic (forward vs strike).
    fwd_intrinsic = np.where(is_call, np.maximum(disc_q - disc_r, 0.0), np.maximum(disc_r - disc_q, 0.0))
    out = np.where(degenerate, fwd_intrinsic, out)
    return out


def greeks(S, K, T, r, q, sigma, option_type) -> dict[str, np.ndarray]:
    """Returns delta, gamma, vega (per vol pt), theta (per day), rho (per 1%)."""
    S, K, T, r, q, sigma, is_call = _prep(S, K, T, r, q, sigma, option_type)
    degenerate = (T <= 0) | (sigma <= 0)

    d1, d2 = d1_d2(S, K, T, r, q, sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(T)
        e_qt = np.exp(-q * T)
        e_rt = np.exp(-r * T)
        n_d1 = _npdf(d1)

        delta_call = e_qt * ndtr(d1)
        delta_put = e_qt * (ndtr(d1) - 1.0)
        delta = np.where(is_call, delta_call, delta_put)

        gamma = e_qt * n_d1 / (S * sigma * sqrt_t)

        vega_annual = S * e_qt * n_d1 * sqrt_t  # per 1.0 (100 vol pts)

        common = -S * e_qt * n_d1 * sigma / (2.0 * sqrt_t)
        theta_call = common - r * K * e_rt * ndtr(d2) + q * S * e_qt * ndtr(d1)
        theta_put = common + r * K * e_rt * ndtr(-d2) - q * S * e_qt * ndtr(-d1)
        theta_annual = np.where(is_call, theta_call, theta_put)

        rho_call = K * T * e_rt * ndtr(d2)
        rho_put = -K * T * e_rt * ndtr(-d2)
        rho_annual = np.where(is_call, rho_call, rho_put)

    # Unit conversions per project convention.
    vega = vega_annual / 100.0
    theta = theta_annual / 365.0
    rho = rho_annual / 100.0

    # Degenerate contracts: delta is the exercise indicator, other Greeks 0.
    itm = np.where(is_call, S * e_qt > K * e_rt, K * e_rt > S * e_qt)
    delta_deg = np.where(is_call, 1.0, -1.0) * itm
    zero = np.zeros_like(S)
    return {
        "delta": np.where(degenerate, delta_deg, delta),
        "gamma": np.where(degenerate, zero, gamma),
        "vega": np.where(degenerate, zero, vega),
        "theta": np.where(degenerate, zero, theta),
        "rho": np.where(degenerate, zero, rho),
    }
