"""Single-shot CLI entry point for the daily Portfolio Ops Digest."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .formatter import render
from .models import DigestInput
from .sources.fx import fetch_fx
from .sources.ledger import load_ledger
from .sources.news import fetch_news
from .sources.positions import load_positions

DEFAULT_TZ = "Asia/Taipei"
log = logging.getLogger(__name__)


def _now_in_tz(tz_name: str) -> datetime:
    """Return current wall-clock time in the given IANA timezone."""
    return datetime.now(tz=ZoneInfo(tz_name))


def build_digest(
    *,
    now: datetime,
    watchlist_path: Path,
    seen_path: Path,
    ledger_path: Path,
    positions_path: Path,
) -> DigestInput:
    """Run all four collectors in isolation and assemble a DigestInput.

    `now` is used for news (datetime; powers lookback_hours and dedup rolloff).
    `today = now.date()` is threaded into the ledger and positions collectors
    so they share the same Asia/Taipei calendar day.
    """
    today = now.date()
    exceptions: list[str] = []

    try:
        news, news_exc = fetch_news(watchlist_path, seen_path, now=now)
        exceptions.extend(news_exc)
    except Exception as e:
        log.exception("news collector raised")
        news = None
        exceptions.append(f"news: unexpected {type(e).__name__}")

    try:
        fx, fx_exc = fetch_fx()
        exceptions.extend(fx_exc)
    except Exception as e:
        log.exception("fx collector raised")
        fx = None
        exceptions.append(f"fx: unexpected {type(e).__name__}")

    try:
        cf, cf_exc = load_ledger(ledger_path, today=today)
        exceptions.extend(cf_exc)
    except Exception as e:
        log.exception("ledger collector raised")
        cf = None
        exceptions.append(f"ledger: unexpected {type(e).__name__}")

    try:
        snap, snap_exc = load_positions(positions_path, today=today)
        exceptions.extend(snap_exc)
    except Exception as e:
        log.exception("positions collector raised")
        snap = None
        exceptions.append(f"positions: unexpected {type(e).__name__}")

    return DigestInput(
        date_str=today.isoformat(),
        news=news,
        fx=fx,
        cashflow=cf,
        snapshot=snap,
        exceptions=exceptions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portfolio-digest")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the digest to stdout instead of pushing to LINE.",
    )
    args = parser.parse_args(argv)

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tz_name = os.environ.get("TIMEZONE", DEFAULT_TZ)
    now = _now_in_tz(tz_name)

    digest = build_digest(
        now=now,
        watchlist_path=Path(os.environ.get("WATCHLIST_PATH", "private/watchlist.yaml")),
        seen_path=Path(os.environ.get("NEWS_SEEN_PATH", "private/.news_seen.json")),
        ledger_path=Path(os.environ.get("LEDGER_PATH", "private/ledger.csv")),
        positions_path=Path(os.environ.get("POSITIONS_PATH", "private/positions.csv")),
    )
    message = render(digest)

    if args.dry_run:
        sys.stdout.write(message)
        return 0

    sys.stderr.write(
        "error: LINE push is not yet implemented (lands in M7).\n"
        "       use --dry-run to render the digest to stdout.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
