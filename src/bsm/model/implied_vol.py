"""Implied volatility solver (Newton fast path, Brent fallback on [1e-4, 5.0]). NaN when no solution."""


def implied_vol(market_price, S, K, T, r, q, option_type):
    raise NotImplementedError
