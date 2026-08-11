from __future__ import annotations

import logging
import time
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

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}"
# SEC EDGAR rejects the default `python-requests/x.y` UA with 403; setting
# any meaningful UA satisfies their fair-access policy. Other feeds ignore
# the header, so we send one consistent value for every fetch.
USER_AGENT = "portfolio-digest-linebot/0.1"
# Cap parallel fetches. Watchlists in the 7-issuer / ~20-feed range fan in
# at one round; the cap protects against pathological configs.
MAX_PARALLEL_FETCHES = 16
# Single retry with a short backoff smooths out transient feed-side blips
# (SEC EDGAR throttles, occasional 5xx from Seeking Alpha, brief connection
# resets). 2 total attempts adds at most ~0.5s of wall time to the slowest
# URL while turning the common "one feed got an unlucky tick" failure into
# a quiet success instead of a Notes/Exceptions line.
FETCH_MAX_ATTEMPTS = 2
FETCH_RETRY_BACKOFF_SEC = 0.5


def _fetch_entries(url: str, timeout: float) -> list[dict[str, Any]]:
    last_exc: requests.RequestException | None = None
    for attempt in range(FETCH_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                url, timeout=timeout, headers={"User-Agent": USER_AGENT}
            )
            resp.raise_for_status()
            parsed = feedparser.parse(resp.text)
            return list(parsed.entries)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < FETCH_MAX_ATTEMPTS:
                time.sleep(FETCH_RETRY_BACKOFF_SEC)
    assert last_exc is not None  # loop above guarantees this
    raise last_exc


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
            entries = _fetch_entries(url, timeout)
            log.debug("news: fetched %d entries from %s", len(entries), url)
            return url, entries
        except (requests.RequestException, ValueError) as exc:
            log.debug("news: fetch failed for %s: %s", url, type(exc).__name__)
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
    issuers = wl["issuers"]
    if not isinstance(issuers, list):
        return None, ["news: watchlist malformed ('issuers' must be a list)"]

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
    for issuer in issuers:
        if not isinstance(issuer, dict) or not issuer.get("enabled", True):
            continue
        # `id` is the canonical label; fall back to `name` so an issuer without
        # an explicit id still produces a usable digest line and isn't silently
        # skipped.
        iid = issuer.get("id") or issuer.get("name") or ""
        if not iid:
            continue

        # `rss` is canonical; `rss_feeds` is accepted as an alias.
        raw_urls = issuer.get("rss") or issuer.get("rss_feeds") or []
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        if isinstance(raw_urls, list):
            configured_urls = [
                url.strip()
                for url in raw_urls
                if isinstance(url, str) and url.strip()
            ]
        else:
            configured_urls = []
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

        # Gather *every* fresh, non-duplicate candidate and scan each for
        # alert keywords — not just the first. With max_items_per_issuer=1
        # the old loop stopped after choosing one item, so a risk headline
        # ranked below the top one never tripped the alert. Dedup state
        # (`seen`) is read here but only mutated for items we actually show,
        # so a candidate we scan-but-don't-display can still surface tomorrow.
        eligible: list[tuple[dict[str, Any], str, bool, str | None]] = []
        n_stale = 0
        n_dup = 0
        for entry, src_url in candidate_entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            ts = _entry_published(entry)
            if ts is None or ts < cutoff:
                n_stale += 1
                continue
            if is_duplicate(title, seen, threshold=threshold):
                n_dup += 1
                continue
            haystack = title.lower()
            # Track the *first* matching keyword so we can log it on display.
            # Without this, a flipped Status only tells the reader "something
            # tripped"; with it, broad / noisy keywords (e.g. "debt" matching
            # "less net debt") are immediately identifiable in the cron log
            # so the watchlist can be tuned by inspection.
            matched_kw: str | None = next(
                (k for k in effective_alert_keywords if k and k in haystack),
                None,
            )
            eligible.append((entry, src_url, matched_kw is not None, matched_kw))

        # Prefer alert headlines for the (capped) display slots so the headline
        # that tripped the alert is the one actually shown. Stable sort keeps
        # feed order within each group, so the no-alert case is unchanged: the
        # first-by-feed-order item is still what's displayed.
        eligible.sort(key=lambda t: not t[2])
        n_alerts = sum(1 for t in eligible if t[2])

        shown = 0
        for entry, src_url, is_alert, matched_kw in eligible:
            if shown >= max_per_issuer:
                break
            title = (entry.get("title") or "").strip()
            # Guard against displaying two near-identical headlines when the
            # cap is > 1 (the first appended makes the second look duplicate).
            if is_duplicate(title, seen, threshold=threshold):
                continue
            seen.append(SeenEntry(title_norm=normalize(title), first_seen=now.isoformat()))
            items.append(
                NewsItem(
                    issuer_id=iid,
                    summary=title,
                    source=_entry_source(entry, src_url),
                    is_alert=is_alert,
                    link=(entry.get("link") or None),
                )
            )
            if is_alert and matched_kw:
                log.info(
                    "news: %s: ALERT shown — %r (keyword: %s)",
                    iid,
                    title[:120],
                    matched_kw,
                )
            shown += 1

        # One summary line per issuer makes "why no news for X today?"
        # answerable straight from the cron log: distinguishes no-data
        # (candidates=0) from everything-too-old (stale>0) from already-seen
        # (dup>0), and surfaces how many eligible headlines tripped a keyword
        # (alerts>0) even when only one is shown.
        log.info(
            "news: %s: candidates=%d eligible=%d shown=%d alerts=%d stale=%d dup=%d",
            iid,
            len(candidate_entries),
            len(eligible),
            shown,
            n_alerts,
            n_stale,
            n_dup,
        )

    if persist_seen:
        save_seen(seen_path, seen)
    return items, exceptions
