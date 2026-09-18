"""Implied volatility solver (Newton fast path, Brent fallback on [1e-4, 5.0]). NaN when no solution.

Vectorized over numpy arrays: all inputs broadcast together. The Newton step is run on the whole
array at once; contracts that fail to converge (or wander out of bounds) fall back to a bracketed
Brent solve in a small Python loop over only those contracts. A contract gets NaN when the market
price is outside the arbitrage-free bounds, i.e. below the zero-vol price or above the sigma=5.0 price.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from bsm.model.black_scholes import _prep, greeks, price

SIGMA_LO = 1e-4
SIGMA_HI = 5.0
_TOL = 1e-14  # relative price tolerance (a few ulps)
_NEAR_TOL = 1e-10  # accept a Newton iterate stuck at roundoff within this relative price error
_SIGMA_TOL = 1e-10
_NEWTON_ITERS = 20


def implied_vol(market_price, S, K, T, r, q, option_type) -> np.ndarray:
    """Black-Scholes implied vol (decimal) from a market price. NaN where no solution exists."""
    S, K, T, r, q, mp, is_call = _prep(S, K, T, r, q, market_price, option_type)
    otype = np.where(is_call, "call", "put")
    n = S.size
    out = np.full(S.shape, np.nan)
    if n == 0:
        return out

    # Arbitrage-free bounds: price must lie between the sigma->0 and sigma=SIGMA_HI prices.
    valid = np.isfinite(mp) & (T > 0) & (S > 0) & (K > 0) & (mp > 0)
    lo_price = price(S, K, T, r, q, SIGMA_LO, otype)
    hi_price = price(S, K, T, r, q, SIGMA_HI, otype)
    valid &= (mp > lo_price) & (mp < hi_price)
    if not valid.any():
        return out

    # --- Newton fast path (vectorized) ---
    # Brenner-Subrahmanyam seed: sigma ~ sqrt(2*pi/T) * price / S, clipped to bounds.
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma = np.sqrt(2.0 * np.pi / np.where(T > 0, T, 1.0)) * mp / S
    sigma = np.clip(np.where(np.isfinite(sigma), sigma, 0.3), 0.05, 2.0)
    converged = np.zeros(S.shape, dtype=bool)
    active = valid.copy()
    for _ in range(_NEWTON_ITERS):
        if not active.any():
            break
        p = price(S, K, T, r, q, sigma, otype)
        v = greeks(S, K, T, r, q, sigma, otype)["vega"] * 100.0  # dPrice/dSigma (per 1.0)
        diff = p - mp
        with np.errstate(divide="ignore", invalid="ignore"):
            step = np.where(v > 1e-12, diff / v, np.inf)
        # Converged when the price matches to float precision OR the sigma step is negligible.
        # (Deep ITM/OTM contracts have tiny vega, so a price tolerance alone leaves sigma sloppy.)
        hit = active & ((np.abs(diff) <= _TOL * mp) | (np.abs(step) < _SIGMA_TOL))
        converged |= hit
        active &= ~hit
        new_sigma = sigma - np.where(active, step, 0.0)
        # Anything that leaves the bracket or stalls is handed to Brent.
        bad = active & (~np.isfinite(new_sigma) | (new_sigma <= SIGMA_LO) | (new_sigma >= SIGMA_HI))
        active &= ~bad
        sigma = np.where(active, new_sigma, sigma)
    # Iterates that are price-accurate to ~1e-10 but can't reach _TOL are roundoff-limited
    # (tiny extrinsic value, tiny vega); accept them rather than paying for a Brent solve.
    if active.any():
        diff = price(S, K, T, r, q, sigma, otype) - mp
        near = active & (np.abs(diff) <= _NEAR_TOL * mp)
        converged |= near
    out[converged] = sigma[converged]

    # --- Brent fallback for anything valid but unconverged ---
    fallback = np.flatnonzero(valid & ~converged)
    flat = [a.ravel() for a in (S, K, T, r, q, mp)]
    otype_flat = otype.ravel()
    out_flat = out.ravel()
    for i in fallback:
        s_, k_, t_, r_, q_, m_ = (a[i] for a in flat)
        ot = otype_flat[i]

        def f(sig, s_=s_, k_=k_, t_=t_, r_=r_, q_=q_, m_=m_, ot=ot):
            return float(price(s_, k_, t_, r_, q_, sig, ot)) - m_

        try:
            out_flat[i] = brentq(f, SIGMA_LO, SIGMA_HI, xtol=1e-10, maxiter=200)
        except (ValueError, RuntimeError):
            out_flat[i] = np.nan
    return out_flat.reshape(S.shape)
