# Black-Scholes Options Tool

Pulls option chains from Yahoo Finance once per weekday, prices every contract with Black-Scholes-Merton,
computes Greeks and implied volatility, flags contracts whose market mid diverges from a realized-vol
BSM price, and writes the results to a Google Sheet. A Tableau Public dashboard reads the sheet
(synced daily) and includes a what-if pricer driven by Tableau parameters.

It's an analysis tool. It never places trades.

```
Yahoo Finance ──► bsm.data.yahoo ──► bsm.pipeline ──► bsm.model (price, Greeks, IV, realized vol)
                                           │
                                           ├──► data/raw/chain_<TICKER>_<ts>.parquet   (raw snapshot)
                                           ├──► data/processed/options_latest.csv      (local mirror)
                                           └──► Google Sheet "BSM Data" ──► Tableau Public (daily sync)
                                                  options_latest  (overwritten each run)
                                                  iv_history      (appended, one row per ticker/expiry/day)
                                                  run_log         (appended)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env    # then point GOOGLE_SERVICE_ACCOUNT_FILE at your key
```

Google credentials: follow [docs/GOOGLE_SETUP.md](docs/GOOGLE_SETUP.md). The pipeline reads the key path from
`GOOGLE_SERVICE_ACCOUNT_FILE` (or the JSON itself from `GOOGLE_SERVICE_ACCOUNT_JSON`). Keys live in
`secrets/`, which is gitignored.

## Usage

```bash
python -m bsm.pipeline                      # fetch, price, push to Google Sheets
python -m bsm.pipeline --no-export          # fetch + price, local CSV only (no Google calls)
python -m bsm.pipeline --tickers AAPL,SPY   # override config.yaml tickers
```

The run prints rows per ticker and exits non-zero only if every ticker failed. A failing ticker is logged
to `run_log` and the rest still publish. `options_latest` is never overwritten with an empty frame,
and re-running on the same day doesn't duplicate `iv_history` rows.

Tickers, expiry count, and filters (min days to expiry, moneyness band, min open interest) are in
[config.yaml](config.yaml).

### Using the model directly

```python
from bsm.model import price, greeks, implied_vol, realized_vol

price(100, 105, 30/365, 0.04, 0.01, 0.30, "call")        # 1.6364
greeks(100, 105, 30/365, 0.04, 0.01, 0.30, "put")         # delta, gamma, vega, theta, rho
implied_vol(1.6364, 100, 105, 30/365, 0.04, 0.01, "call") # ~0.30
```

All functions are vectorized over numpy arrays. Units: rates and vols are decimals, T is calendar
days / 365, theta is per calendar day, vega per vol point, rho per 1% rate move.

## Scheduling

- **GitHub Actions** ([.github/workflows/daily_refresh.yml](.github/workflows/daily_refresh.yml)) runs at
  21:30 UTC Monday to Friday, which is after the US close in both summer and winter time. Add the repo secret
  `GOOGLE_SERVICE_ACCOUNT_JSON` containing the full key file. Trigger a manual run from the Actions tab
  with **Run workflow**. It has optional ticker-override and dry-run inputs.
- **Local fallback** for when Yahoo blocks GitHub's runner IPs: `scripts/install_launchd.sh` installs a
  launchd job that runs `scripts/run_scheduler.py --once` at 16:35 local time on weekdays.
  Remove it with `scripts/install_launchd.sh --uninstall`.

## Tableau dashboard

[tableau/BUILD_GUIDE.md](tableau/BUILD_GUIDE.md) walks through each sheet: volatility smile, IV surface and
term structure, Greeks by strike, mispricing scanner, what-if pricer, payoff diagram, and IV vs realized vol.
It includes the exact calculated-field formulas for the pricer.

## Tests

```bash
pytest                    # model, pipeline transforms, sheet serialization (offline)
BSM_SMOKE=1 pytest        # also hits Yahoo for one ticker
```

The model tests cover put-call parity to 1e-8, Hull textbook values, Greeks against finite differences,
and the implied-vol round trip.

## Screenshots

Screenshots go in `docs/screenshots/` once the dashboard is built and published.

## Caveats

- Yahoo Finance isn't an official API. Quotes are delayed about 15 minutes and the schema occasionally changes.
- BSM assumes European exercise, but most US equity options are American. Treat model values as approximations,
  especially for deep in-the-money puts and dividend payers.
- "Mispricing" compares the market mid with a BSM price at 30-day realized vol. It measures the volatility
  premium, not an arbitrage.

Full design: [docs/SPEC.md](docs/SPEC.md).
