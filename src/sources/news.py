from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Cap parallel fetches. Watchlists in the 7-issuer / ~20-feed range fan in
# at one round; the cap protects against pathological configs.
MAX_PARALLEL_FETCHES = 16


def _fetch_entries(url: str, timeout: float) -> list[dict[str, Any]]:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return list(parsed.entries)


def _fetch_all(
    urls: set[str], timeout: float
) -> dict[str, list[dict[str, Any]] | Exception]:
    """Fetch every URL once, in parallel. Returns url -> entries on success
    or url -> Exception on failure; callers attribute exceptions to the
    right issuer at result-assembly time."""
    if not urls:
        return {}

    def _one(url: str) -> tuple[str, list[dict[str, Any]] | Exception]:
        try:
            return url, _fetch_entries(url, timeout)
        except (requests.RequestException, ValueError) as exc:
            return url, exc

    results: dict[str, list[dict[str, Any]] | Exception] = {}
    workers = min(MAX_PARALLEL_FETCHES, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, u) for u in urls]
        for fut in as_completed(futures):
            url, result = fut.result()
            results[url] = result
    return results


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
    persist_seen: bool = True,
) -> tuple[list[NewsItem] | None, list[str]]:
    """Fetch per-issuer news with cross-run deduplication.

    Returns (list, exceptions) on success. The list may be empty if no fresh
    items survive the dedup filter; that's a "no items" state, distinct from
    None which means the watchlist itself was unusable.

    `persist_seen` controls whether newly-rendered items get written back to
    seen_path. Default True is right for the daily push (so tomorrow's run
    doesn't re-send the same headlines). Pass False for dry-run previews so
    the preview doesn't consume dedup state — otherwise running --dry-run
    immediately followed by --push would surface different items in each.
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

    # ---- Planning pass: build per-issuer plans without doing any I/O. ----
    plans: list[dict[str, Any]] = []
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
        plans.append({
            "iid": iid,
            "configured_urls": configured_urls,
            "gn_url": gn_url,
            "effective_alert_keywords": global_alert_keywords + issuer_alert_keywords,
        })

    # ---- Phase 1: fetch every direct feed in parallel. ----
    direct_urls: set[str] = set()
    for p in plans:
        direct_urls.update(p["configured_urls"])
    fetched_direct = _fetch_all(direct_urls, timeout)

    # ---- Phase 2: parallel-fetch GN URLs only for issuers whose direct feeds
    # yielded zero entries (preserves the original "GN as fallback" semantics
    # so we don't change which item wins the per-issuer slot). ----
    needs_gn: set[str] = set()
    for p in plans:
        has_entries = any(
            isinstance(fetched_direct.get(u), list) and fetched_direct[u]
            for u in p["configured_urls"]
        )
        if not has_entries:
            needs_gn.add(p["gn_url"])
    fetched_gn = _fetch_all(needs_gn, timeout)

    # ---- Result assembly: dedup/lookback/alert logic, identical to before. ----
    for p in plans:
        iid = p["iid"]
        effective_alert_keywords = p["effective_alert_keywords"]

        candidate_entries: list[tuple[dict[str, Any], str]] = []
        for url in p["configured_urls"]:
            res = fetched_direct.get(url)
            if isinstance(res, Exception):
                exceptions.append(f"news: {iid}: {type(res).__name__} on {url}")
                continue
            for entry in (res or []):
                candidate_entries.append((entry, url))
        if not candidate_entries:
            res = fetched_gn.get(p["gn_url"])
            if isinstance(res, Exception):
                exceptions.append(
                    f"news: {iid}: {type(res).__name__} on Google News fallback"
                )
            else:
                for entry in (res or []):
                    candidate_entries.append((entry, p["gn_url"]))

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

    if persist_seen:
        save_seen(seen_path, seen)
    return items, exceptions
