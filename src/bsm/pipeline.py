"""Fetch -> price -> export. One row per contract per snapshot; schema in docs/SPEC.md.

Usage:
    python -m bsm.pipeline                 # full run, pushes to Google Sheets
    python -m bsm.pipeline --no-export     # fetch + price + local CSV only (no Google API calls)
    python -m bsm.pipeline --config other.yaml --tickers AAPL,SPY

The pure transformation (`build_contract_table`, `build_iv_summary`) is separated from the I/O so
it can be unit-tested on a synthetic chain.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from dotenv import find_dotenv, load_dotenv

from bsm.data import yahoo
from bsm.model.black_scholes import greeks, intrinsic, price
from bsm.model.implied_vol import implied_vol
from bsm.model.volatility import realized_vol

log = logging.getLogger("bsm.pipeline")

# The Tableau schema. Order matters: Google Sheet headers must stay stable (see SPEC).
OUTPUT_COLUMNS = [
    "snapshot_ts", "ticker", "contract_symbol", "type", "expiry",
    "days_to_expiry", "T_years",
    "spot", "strike", "moneyness", "log_moneyness",
    "bid", "ask", "mid", "last", "spread_pct",
    "volume", "open_interest",
    "r", "q",
    "iv_yahoo", "iv_model", "realized_vol_30d",
    "bs_price_realized", "mispricing", "mispricing_pct",
    "delta", "gamma", "vega", "theta", "rho",
    "intrinsic", "time_value",
]

IV_HISTORY_COLUMNS = [
    "snapshot_ts", "date", "ticker", "expiry", "days_to_expiry", "spot",
    "atm_iv", "iv_call_25d", "iv_put_25d", "skew_25d",
    "realized_vol_30d", "vol_risk_premium", "r", "n_contracts",
]


# --------------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------------

def load_config(path: str | Path = "config.yaml") -> dict:
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("options", {})
    cfg.setdefault("risk_free", {}).setdefault("fallback", 0.04)
    cfg.setdefault("volatility", {}).setdefault("realized_window_days", 30)
    cfg.setdefault("export", {}).setdefault("csv_dir", "data/processed")
    cfg["export"].setdefault("google_sheets", {}).setdefault("spreadsheet_name", "BSM Data")
    cfg.setdefault("raw_dir", "data/raw")
    return cfg


# --------------------------------------------------------------------------------------------
# Pure transforms
# --------------------------------------------------------------------------------------------

def build_contract_table(
    chain: pd.DataFrame,
    *,
    ticker: str,
    spot: float,
    r: float,
    q: float,
    rv: float,
    snapshot_ts: pd.Timestamp,
    min_days_to_expiry: int = 1,
    moneyness_band: float = 0.30,
    min_open_interest: int = 0,
) -> pd.DataFrame:
    """Turn a raw yfinance chain (calls+puts stacked, with `type`/`expiry`) into the output schema.

    Filters (from config): DTE >= min_days_to_expiry, |K/S - 1| <= moneyness_band,
    open_interest >= min_open_interest, bid > 0. Everything is vectorized with numpy.

    Greeks are evaluated at iv_model; where the solver returns NaN (price at or below the
    no-arbitrage bound, typically deep ITM with no extrinsic value) Yahoo's IV is used instead so
    the Greeks columns aren't blank for those contracts.
    """
    if chain.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = pd.DataFrame({
        "contract_symbol": chain["contractSymbol"].astype(str).to_numpy(),
        "type": chain["type"].astype(str).str.lower().to_numpy(),
        "expiry": pd.to_datetime(chain["expiry"]).dt.tz_localize(None).dt.normalize().to_numpy(),
        "strike": chain["strike"].astype(float).to_numpy(),
        "bid": chain["bid"].astype(float).to_numpy(),
        "ask": chain["ask"].astype(float).to_numpy(),
        "last": chain["lastPrice"].astype(float).to_numpy(),
        "volume": chain["volume"].fillna(0).astype(float).to_numpy(),
        "open_interest": chain["openInterest"].fillna(0).astype(float).to_numpy(),
        "iv_yahoo": chain["impliedVolatility"].astype(float).to_numpy(),
    })

    snapshot_date = pd.Timestamp(snapshot_ts).tz_localize(None).normalize() if pd.Timestamp(snapshot_ts).tz is None \
        else pd.Timestamp(snapshot_ts).tz_convert("America/New_York").tz_localize(None).normalize()
    df["days_to_expiry"] = (df["expiry"] - snapshot_date).dt.days.astype(int)
    df["T_years"] = df["days_to_expiry"] / 365.0
    df["spot"] = float(spot)
    df["moneyness"] = df["strike"] / spot

    # --- filters ---
    keep = (
        (df["days_to_expiry"] >= min_days_to_expiry)
        & ((df["moneyness"] - 1.0).abs() <= moneyness_band)
        & (df["open_interest"] >= min_open_interest)
        & (df["bid"] > 0)
        & np.isfinite(df["ask"]) & (df["ask"] >= df["bid"])
    )
    df = df.loc[keep].reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    S = df["spot"].to_numpy()
    K = df["strike"].to_numpy()
    T = df["T_years"].to_numpy()
    otype = df["type"].to_numpy()

    df["log_moneyness"] = np.log(K / S)
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"]
    df["r"] = float(r)
    df["q"] = float(q)

    mid = df["mid"].to_numpy()
    iv_model = implied_vol(mid, S, K, T, r, q, otype)
    df["iv_model"] = iv_model

    rv = float(rv) if rv is not None and np.isfinite(rv) else np.nan
    df["realized_vol_30d"] = rv
    bs_rv = price(S, K, T, r, q, rv, otype) if np.isfinite(rv) else np.full(len(df), np.nan)
    df["bs_price_realized"] = bs_rv
    df["mispricing"] = mid - bs_rv
    df["mispricing_pct"] = df["mispricing"] / mid  # relative to the market mid (always > 0 here)

    sigma_for_greeks = np.where(np.isfinite(iv_model), iv_model, df["iv_yahoo"].to_numpy())
    g = greeks(S, K, T, r, q, sigma_for_greeks, otype)
    for name in ("delta", "gamma", "vega", "theta", "rho"):
        df[name] = g[name]

    df["intrinsic"] = intrinsic(S, K, otype)
    df["time_value"] = mid - df["intrinsic"]

    df["snapshot_ts"] = pd.Timestamp(snapshot_ts)
    df["ticker"] = ticker
    df = df[OUTPUT_COLUMNS].sort_values(["expiry", "type", "strike"]).reset_index(drop=True)
    return df


def _interp_iv_at_delta(deltas: np.ndarray, ivs: np.ndarray, target: float) -> float:
    """IV at a target delta by linear interpolation across strikes; NaN if target is outside the range."""
    ok = np.isfinite(deltas) & np.isfinite(ivs)
    if ok.sum() < 2:
        return np.nan
    d, v = deltas[ok], ivs[ok]
    order = np.argsort(d)
    d, v = d[order], v[order]
    if not (d[0] <= target <= d[-1]):
        return np.nan
    return float(np.interp(target, d, v))


def build_iv_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ticker/expiry for `iv_history`: ATM IV, 25-delta call/put IV, skew, realized vol, spot."""
    if df.empty:
        return pd.DataFrame(columns=IV_HISTORY_COLUMNS)
    rows = []
    for (ticker, expiry), grp in df.groupby(["ticker", "expiry"], sort=True):
        spot = float(grp["spot"].iloc[0])
        valid = grp[np.isfinite(grp["iv_model"])]
        # ATM: the strike nearest spot; average call and put IV at that strike when both exist.
        if valid.empty:
            atm_iv = np.nan
        else:
            nearest = valid.loc[(valid["strike"] - spot).abs().idxmin(), "strike"]
            atm_iv = float(valid.loc[valid["strike"] == nearest, "iv_model"].mean())
        calls = valid[valid["type"] == "call"]
        puts = valid[valid["type"] == "put"]
        iv_c25 = _interp_iv_at_delta(calls["delta"].to_numpy(), calls["iv_model"].to_numpy(), 0.25)
        iv_p25 = _interp_iv_at_delta(puts["delta"].to_numpy(), puts["iv_model"].to_numpy(), -0.25)
        rv = float(grp["realized_vol_30d"].iloc[0])
        snapshot_ts = pd.Timestamp(grp["snapshot_ts"].iloc[0])
        rows.append({
            "snapshot_ts": snapshot_ts,
            "date": snapshot_ts.tz_convert("America/New_York").date() if snapshot_ts.tz is not None else snapshot_ts.date(),
            "ticker": ticker,
            "expiry": pd.Timestamp(expiry),
            "days_to_expiry": int(grp["days_to_expiry"].iloc[0]),
            "spot": spot,
            "atm_iv": atm_iv,
            "iv_call_25d": iv_c25,
            "iv_put_25d": iv_p25,
            "skew_25d": iv_p25 - iv_c25,
            "realized_vol_30d": rv,
            "vol_risk_premium": atm_iv - rv,
            "r": float(grp["r"].iloc[0]),
            "n_contracts": int(len(grp)),
        })
    return pd.DataFrame(rows, columns=IV_HISTORY_COLUMNS)


# --------------------------------------------------------------------------------------------
# Per-ticker orchestration
# --------------------------------------------------------------------------------------------

def process_ticker(ticker: str, cfg: dict, r: float, snapshot_ts: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch everything for one ticker and return (contract_table, raw_chain)."""
    opts = cfg["options"]
    window = int(cfg["volatility"]["realized_window_days"])

    spot = yahoo.get_spot(ticker)
    q = yahoo.get_dividend_yield(ticker)
    hist = yahoo.get_history(ticker, window + 1)  # window returns need window+1 closes
    rv = realized_vol(hist["Close"], window)
    chain = yahoo.get_option_chain(
        ticker, int(opts.get("max_expiries", 6)), int(opts.get("min_days_to_expiry", 1))
    )
    table = build_contract_table(
        chain,
        ticker=ticker, spot=spot, r=r, q=q, rv=rv, snapshot_ts=snapshot_ts,
        min_days_to_expiry=int(opts.get("min_days_to_expiry", 1)),
        moneyness_band=float(opts.get("moneyness_band", 0.30)),
        min_open_interest=int(opts.get("min_open_interest", 0)),
    )
    log.info("%s: spot=%.2f q=%.4f rv=%.4f chain=%d rows -> %d after filters",
             ticker, spot, q, rv, len(chain), len(table))
    return table, chain


def _save_raw(chain: pd.DataFrame, ticker: str, snapshot_ts: pd.Timestamp, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot_ts.strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"chain_{ticker}_{stamp}.parquet"
    chain.to_parquet(path, index=False)
    return path


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------

def run(config_path: str = "config.yaml", *, export: bool = True, tickers: list[str] | None = None) -> dict:
    """Run the pipeline once. Returns a status dict (also appended to the sheet's run_log)."""
    load_dotenv(find_dotenv(usecwd=True))
    cfg = load_config(config_path)
    tickers = tickers or list(cfg["tickers"])
    snapshot_ts = pd.Timestamp.now(tz="UTC").floor("s")
    t0 = time.time()
    raw_dir = Path(cfg["raw_dir"])
    csv_dir = Path(cfg["export"]["csv_dir"])
    csv_dir.mkdir(parents=True, exist_ok=True)

    r = yahoo.get_risk_free_rate(fallback=float(cfg["risk_free"]["fallback"]))
    log.info("snapshot %s  r=%.4f  tickers=%s", snapshot_ts.isoformat(), r, tickers)

    tables: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    for ticker in tickers:
        try:
            table, chain = process_ticker(ticker, cfg, r, snapshot_ts)
            _save_raw(chain, ticker, snapshot_ts, raw_dir)
            if table.empty:
                raise ValueError("no contracts survived the filters")
            tables.append(table)
        except Exception as exc:  # noqa: BLE001 - keep the other tickers going
            log.exception("%s failed", ticker)
            errors[ticker] = f"{type(exc).__name__}: {exc}"

    options = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=OUTPUT_COLUMNS)
    summary = build_iv_summary(options)
    counts = options.groupby("ticker").size().to_dict() if not options.empty else {}

    options.to_csv(csv_dir / "options_latest.csv", index=False)
    summary.to_csv(csv_dir / "iv_history_latest.csv", index=False)

    status = {
        "snapshot_ts": snapshot_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ok" if not errors else ("partial" if tables else "failed"),
        "rows_written": int(len(options)),
        "tickers_ok": ",".join(sorted(counts)),
        "tickers_failed": ",".join(sorted(errors)),
        "errors": "; ".join(f"{k}: {v}" for k, v in errors.items()),
        "row_counts": counts,
        "risk_free": r,
        "exported": False,
    }

    if export:
        from bsm.export import sheets  # imported lazily so --no-export needs no Google creds

        name = cfg["export"]["google_sheets"]["spreadsheet_name"]
        try:
            if not options.empty:
                sheets.write_options_latest(options, name)
                sheets.append_iv_history(summary, name)
                status["exported"] = True
            else:
                log.error("no data for any ticker; leaving options_latest untouched")
        except Exception as exc:  # noqa: BLE001
            log.exception("Google Sheets export failed")
            status["status"] = "failed" if status["status"] == "ok" else status["status"]
            status["errors"] = (status["errors"] + "; " if status["errors"] else "") + f"export: {type(exc).__name__}: {exc}"
        status["duration_s"] = round(time.time() - t0, 1)
        try:
            sheets.append_run_log(status, name)
        except Exception as exc:  # noqa: BLE001
            log.error("run_log append failed: %s", exc)
    else:
        status["duration_s"] = round(time.time() - t0, 1)

    _print_report(status)
    return status


def _print_report(status: dict) -> None:
    print(f"\nsnapshot {status['snapshot_ts']}  status={status['status']}  "
          f"rows={status['rows_written']}  exported={status['exported']}  ({status['duration_s']}s)")
    print(f"{'ticker':<8}{'rows':>6}")
    for tk, n in sorted(status["row_counts"].items()):
        print(f"{tk:<8}{n:>6}")
    if status["errors"]:
        print("errors:", status["errors"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BSM options pipeline: Yahoo -> BSM -> Google Sheets")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-export", action="store_true", help="skip Google Sheets (local CSV only)")
    ap.add_argument("--tickers", help="comma-separated override of config tickers")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    status = run(args.config, export=not args.no_export, tickers=tickers)
    return 0 if status["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
