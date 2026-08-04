"""Single-shot CLI entry point for the daily Portfolio Ops Digest."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .formatter import render
from .line_client import push_message
from .llm import DEFAULT_MODEL, analyze_news, fallback_greeting, generate_greeting
from .models import DigestInput
from .sources.fx import fetch_fx
from .sources.ledger import load_ledger
from .sources.news import fetch_news
from .sources.positions import load_positions
from .sources.schedule import project_cashflows

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
    persist_seen: bool = True,
    recurring_path: Path | None = None,
    projection_days: int = 60,
    llm_api_key: str | None = None,
    llm_model: str = DEFAULT_MODEL,
    llm_context: str | None = None,
) -> DigestInput:
    """Run all four collectors in isolation and assemble a DigestInput.

    `now` is used for news (datetime; powers lookback_hours and dedup rolloff).
    `today = now.date()` is threaded into the ledger and positions collectors
    so they share the same Asia/Taipei calendar day.

    `persist_seen` is forwarded to the news collector. Set False for previews
    (--dry-run / default) so the preview doesn't consume dedup state.
    """
    today = now.date()
    exceptions: list[str] = []

    # M11 — morning greeting. Always present: LLM-generated when a key is
    # set, deterministic offline rotation otherwise.
    if llm_api_key:
        try:
            greeting, greet_exc = generate_greeting(
                today, api_key=llm_api_key, model=llm_model
            )
            exceptions.extend(greet_exc)
        except Exception as e:
            log.exception("greeting raised")
            greeting = fallback_greeting(today)
            exceptions.append(f"llm greeting: unexpected {type(e).__name__}")
    else:
        greeting = fallback_greeting(today)

    try:
        news, news_exc = fetch_news(
            watchlist_path, seen_path, now=now, persist_seen=persist_seen
        )
        exceptions.extend(news_exc)
    except Exception as e:
        log.exception("news collector raised")
        news = None
        exceptions.append(f"news: unexpected {type(e).__name__}")

    # M10 — LLM impact analysis. Strictly additive: no key or any failure
    # leaves `news` exactly as fetched.
    news_overview: str | None = None
    if news and llm_api_key:
        try:
            news, news_overview, llm_exc = analyze_news(
                news,
                api_key=llm_api_key,
                model=llm_model,
                context=llm_context,
            )
            exceptions.extend(llm_exc)
        except Exception as e:
            log.exception("llm analysis raised")
            exceptions.append(f"llm: unexpected {type(e).__name__}")

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

    # M9 — projected cashflow calendar (bond coupons from positions.csv,
    # everything else from recurring.csv).
    try:
        projected, proj_exc = project_cashflows(
            positions_path,
            recurring_path if recurring_path is not None else Path("private/recurring.csv"),
            today=today,
            horizon_days=projection_days,
        )
        exceptions.extend(proj_exc)
    except Exception as e:
        log.exception("schedule collector raised")
        projected = None
        exceptions.append(f"schedule: unexpected {type(e).__name__}")

    return DigestInput(
        date_str=today.isoformat(),
        news=news,
        fx=fx,
        cashflow=cf,
        snapshot=snap,
        exceptions=exceptions,
        projected=projected,
        news_overview=news_overview,
        greeting=greeting,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portfolio-digest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the digest to stdout (the default; explicit alias).",
    )
    mode.add_argument(
        "--push",
        action="store_true",
        help="Render the digest AND push it to the configured LINE group.",
    )
    args = parser.parse_args(argv)

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, None)
    if not isinstance(log_level, int):
        sys.stderr.write(f"error: invalid LOG_LEVEL: {log_level_name!r}\n")
        return 2
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    tz_name = os.environ.get("TIMEZONE", DEFAULT_TZ)
    try:
        now = _now_in_tz(tz_name)
    except ZoneInfoNotFoundError:
        sys.stderr.write(f"error: invalid TIMEZONE: {tz_name!r}\n")
        return 2

    try:
        projection_days = int(os.environ.get("PROJECTION_DAYS", "60"))
    except ValueError:
        sys.stderr.write("error: invalid PROJECTION_DAYS\n")
        return 2

    llm_context: str | None = None
    context_path = Path(
        os.environ.get("LLM_CONTEXT_PATH", "private/llm_context.txt")
    )
    if context_path.exists():
        try:
            llm_context = context_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            llm_context = None

    digest = build_digest(
        now=now,
        watchlist_path=Path(os.environ.get("WATCHLIST_PATH", "private/watchlist.yaml")),
        seen_path=Path(os.environ.get("NEWS_SEEN_PATH", "private/.news_seen.json")),
        ledger_path=Path(os.environ.get("LEDGER_PATH", "private/ledger.csv")),
        positions_path=Path(os.environ.get("POSITIONS_PATH", "private/positions.csv")),
        # Only --push commits dedup state. Previews (default and --dry-run)
        # leave seen.json untouched so re-running yields the same items.
        persist_seen=args.push,
        recurring_path=Path(os.environ.get("RECURRING_PATH", "private/recurring.csv")),
        projection_days=projection_days,
        llm_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        llm_model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        llm_context=llm_context,
    )
    message = render(digest)

    if not args.push:
        # Default and --dry-run both stay safe: render to stdout, no push.
        sys.stdout.write(message)
        return 0

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    group_id = os.environ.get("LINE_GROUP_ID", "")
    if not token or not group_id:
        sys.stderr.write(
            "error: --push requires LINE_CHANNEL_ACCESS_TOKEN and LINE_GROUP_ID in env.\n"
        )
        return 2

    ok, err = push_message(text=message, group_id=group_id, access_token=token)
    if not ok:
        sys.stderr.write(f"error: LINE push failed: {err}\n")
        return 3
    sys.stdout.write(message)
    sys.stdout.write("\n(pushed to LINE)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
