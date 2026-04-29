from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
import yaml

from ..dedup import SeenEntry, is_duplicate, load_seen, normalize, prune_old, save_seen
from ..models import NewsItem

DEFAULT_TIMEOUT = 10.0
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}"
# SEC EDGAR rejects the default `python-requests/x.y` UA with 403; setting
# any meaningful UA satisfies their fair-access policy. Other feeds ignore
# the header, so we send one consistent value for every fetch.
USER_AGENT = "portfolio-digest-linebot/0.1"


def _fetch_entries(url: str, timeout: float) -> list[dict[str, Any]]:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return list(parsed.entries)


def _entry_published(entry: dict[str, Any]) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _entry_source(entry: dict[str, Any], fallback_url: str) -> str:
    src = entry.get("source")
    if isinstance(src, dict):
        title = src.get("title")
        if title:
            return title
    link = entry.get("link") or fallback_url
    netloc = urlparse(link).netloc
    return netloc or fallback_url


def fetch_news(
    watchlist_path: Path,
    seen_path: Path,
    *,
    now: datetime,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[NewsItem] | None, list[str]]:
    """Fetch per-issuer news with cross-run deduplication.

    Returns (list, exceptions) on success. The list may be empty if no fresh
    items survive the dedup filter; that's a "no items" state, distinct from
    None which means the watchlist itself was unusable.
    """
    if not watchlist_path.exists():
        return None, ["news: watchlist file not found"]

    try:
        with watchlist_path.open(encoding="utf-8") as f:
            wl = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        return None, [f"news: watchlist read error: {type(e).__name__}"]

    if not isinstance(wl, dict) or "issuers" not in wl:
        return None, ["news: watchlist malformed (missing 'issuers')"]

    settings = wl.get("settings") or {}
    # `lookback_hours` is canonically under settings, but a top-level value is
    # accepted as an alias so a flat watchlist still works.
    lookback_hours = int(settings.get("lookback_hours", wl.get("lookback_hours", 24)))
    max_per_issuer = int(settings.get("max_items_per_issuer", 1))
    dedup_days = int(settings.get("dedup_lookback_days", 3))
    threshold = float(settings.get("similarity_threshold", 0.85))
    global_alert_keywords = [str(k).lower() for k in (settings.get("alert_keywords") or [])]

    seen = prune_old(load_seen(seen_path), now=now, days=dedup_days)
    cutoff = now - timedelta(hours=lookback_hours)

    items: list[NewsItem] = []
    exceptions: list[str] = []

    for issuer in wl["issuers"]:
        if not isinstance(issuer, dict) or not issuer.get("enabled", True):
            continue
        # `id` is the canonical label; fall back to `name` so an issuer without
        # an explicit id still produces a usable digest line and isn't silently
        # skipped.
        iid = issuer.get("id") or issuer.get("name") or ""
        if not iid:
            continue

        # `rss` is canonical; `rss_feeds` is accepted as an alias.
        configured_urls = list(issuer.get("rss") or issuer.get("rss_feeds") or [])
        # `google_news_query` is canonical; `query` is accepted as an alias.
        gn_query = (
            issuer.get("google_news_query")
            or issuer.get("query")
            or issuer.get("name")
            or iid
        )
        gn_url = GOOGLE_NEWS_RSS.format(q=quote_plus(str(gn_query)))

        # Per-issuer alert_keywords stack on top of the global list so a
        # watchlist can mix shared terms (e.g. "downgrade") with issuer-
        # specific ones (e.g. Chinese-language regulatory terms).
        issuer_alert_keywords = [
            str(k).lower() for k in (issuer.get("alert_keywords") or [])
        ]
        effective_alert_keywords = global_alert_keywords + issuer_alert_keywords

        # Try configured RSS feeds first; fall back to Google News only if none yielded entries.
        candidate_entries: list[tuple[dict[str, Any], str]] = []
        for url in configured_urls:
            try:
                entries = _fetch_entries(url, timeout)
            except (requests.RequestException, ValueError) as exc:
                exceptions.append(f"news: {iid}: {type(exc).__name__} on {url}")
                continue
            for entry in entries:
                candidate_entries.append((entry, url))
        if not candidate_entries:
            try:
                entries = _fetch_entries(gn_url, timeout)
                for entry in entries:
                    candidate_entries.append((entry, gn_url))
            except (requests.RequestException, ValueError) as exc:
                exceptions.append(f"news: {iid}: {type(exc).__name__} on Google News fallback")

        chosen = 0
        for entry, src_url in candidate_entries:
            if chosen >= max_per_issuer:
                break
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            ts = _entry_published(entry)
            if ts is None or ts < cutoff:
                continue
            if is_duplicate(title, seen, threshold=threshold):
                continue

            seen.append(SeenEntry(title_norm=normalize(title), first_seen=now.isoformat()))
            haystack = title.lower()
            is_alert = any(k in haystack for k in effective_alert_keywords if k)
            items.append(
                NewsItem(
                    issuer_id=iid,
                    summary=title,
                    source=_entry_source(entry, src_url),
                    is_alert=is_alert,
                    link=(entry.get("link") or None),
                )
            )
            chosen += 1

    save_seen(seen_path, seen)
    return items, exceptions
