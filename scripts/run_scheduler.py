"""Local fallback scheduler: run bsm.pipeline once per weekday after US close.

The primary schedule is GitHub Actions (.github/workflows/daily_refresh.yml). Use this when Yahoo
rate-limits or blocks GitHub's runner IPs.

Two modes:
    python scripts/run_scheduler.py --once      # run now if today is a weekday (what launchd calls)
    python scripts/run_scheduler.py --loop      # stay running; fire at --at (ET) every weekday

launchd is the recommended way on macOS (survives reboots, no terminal needed):
    scripts/install_launchd.sh        # installs com.bsm.daily-refresh, 16:35 local time Mon-Fri
    scripts/install_launchd.sh --uninstall
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ET = ZoneInfo("America/New_York")
log = logging.getLogger("bsm.scheduler")


def is_weekday(d: dt.date) -> bool:
    return d.weekday() < 5


def next_fire_time(now: dt.datetime, at: dt.time) -> dt.datetime:
    """Next weekday at `at` (ET) strictly after `now`."""
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    while not is_weekday(candidate.date()):
        candidate += dt.timedelta(days=1)
    return candidate


def run_once(config: str, force: bool = False) -> int:
    from bsm.data import yahoo
    from bsm.pipeline import run

    today = dt.datetime.now(ET).date()
    if not force and not is_weekday(today):
        log.info("%s is a weekend; skipping", today)
        return 0
    yahoo.clear_cache()
    status = run(config)
    return 0 if status["status"] != "failed" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run now (weekdays only) and exit")
    mode.add_argument("--loop", action="store_true", help="run every weekday at --at and keep running")
    ap.add_argument("--at", default="16:35", help="ET fire time for --loop (HH:MM, default 16:35)")
    ap.add_argument("--force", action="store_true", help="with --once: run even on a weekend")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

    if args.once:
        return run_once(args.config, force=args.force)

    at = dt.time.fromisoformat(args.at)
    while True:
        now = dt.datetime.now(ET)
        fire = next_fire_time(now, at)
        log.info("next run at %s", fire.isoformat())
        time.sleep(max(1.0, (fire - now).total_seconds()))
        try:
            run_once(args.config)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("pipeline run failed")


if __name__ == "__main__":
    sys.exit(main())
