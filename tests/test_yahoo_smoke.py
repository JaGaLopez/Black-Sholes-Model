"""Live smoke test against Yahoo for one ticker. Skipped unless BSM_SMOKE=1 (needs network)."""
import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("BSM_SMOKE") != "1", reason="set BSM_SMOKE=1 to hit Yahoo")

TICKER = "AAPL"


def test_spot_and_rates():
    from bsm.data import yahoo

    spot = yahoo.get_spot(TICKER)
    assert spot > 0
    assert yahoo.get_spot(TICKER) == spot  # cached
    q = yahoo.get_dividend_yield(TICKER)
    assert 0.0 <= q < 0.10
    r = yahoo.get_risk_free_rate(fallback=0.04)
    assert 0.0 <= r <= 0.25


def test_history_and_chain():
    from bsm.data import yahoo

    hist = yahoo.get_history(TICKER, 30)
    assert len(hist) == 30 and "Close" in hist.columns
    assert hist.index.is_monotonic_increasing

    chain = yahoo.get_option_chain(TICKER, max_expiries=2)
    assert set(chain["type"]) == {"call", "put"}
    assert chain["expiry"].nunique() == 2
    assert (pd.to_datetime(chain["expiry"]) > pd.Timestamp.today().normalize()).all()
    for col in yahoo.CHAIN_COLUMNS:
        assert col in chain.columns, col
    assert np.isfinite(chain["strike"]).all()
