"""Vectorized Black-Scholes-Merton pricing and Greeks. See docs/SPEC.md for formulas and units."""
import numpy as np


def price(S, K, T, r, q, sigma, option_type) -> np.ndarray:
    raise NotImplementedError


def greeks(S, K, T, r, q, sigma, option_type) -> dict[str, np.ndarray]:
    """Returns delta, gamma, vega (per vol pt), theta (per day), rho (per 1%)."""
    raise NotImplementedError
