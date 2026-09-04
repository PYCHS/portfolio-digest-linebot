import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from src.dedup import SeenEntry, save_seen
from src.sources.news import fetch_news

UTC = timezone.utc
NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"
WATCHLIST = FIXTURES / "watchlist_test.yaml"
ACME_RSS_URL = "https://example.com/acme/press.rss"
GN_URL = "https://news.google.com/rss/search"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_happy_path_returns_one_item_per_enabled_issuer(requests_mock, tmp_path):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, exc = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    assert exc == []
    assert items is not None
    assert [i.issuer_id for i in items] == ["ACME", "BETA"]
    assert items[0].summary == "ACME Q1 results in line with guidance"
    assert items[0].is_alert is False
    assert items[1].summary == "Beta Capital appoints new CFO"
    assert items[1].source == "Reuters"


def test_seen_write_failure_keeps_collected_news(requests_mock, tmp_path, monkeypatch):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    def fail_save(*args):
        raise PermissionError("simulated read-only state directory")

    monkeypatch.setattr("src.sources.news.save_seen", fail_save)
    items, exc = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)

    assert [i.issuer_id for i in items] == ["ACME", "BETA"]
    assert exc == ["news: dedup state save failed: PermissionError"]


def test_disabled_issuer_skipped(requests_mock, tmp_path):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    # Watchlist has 3 issuers (ACME, BETA, GAMMA-disabled). Only 2 returned.
    assert len(items) == 2
    assert "GAMMA" not in {i.issuer_id for i in items}


def test_lookback_filter_excludes_old_items(requests_mock, tmp_path):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    # rss_acme.xml has an item 28h old that must NOT appear
    assert all("minor product update" not in i.summary for i in items)


def test_future_publication_is_not_consumed_before_it_is_due(requests_mock, tmp_path):
    rss = _read("rss_acme.xml").replace(
        "Sat, 25 Apr 2026 10:00:00 +0000",
        "Sun, 26 Apr 2026 10:00:00 +0000",
    )
    requests_mock.get(ACME_RSS_URL, text=rss)
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))
    seen = tmp_path / "seen.json"

    items, exc = fetch_news(WATCHLIST, seen, now=NOW)
    assert exc == []
    assert all(i.issuer_id != "ACME" for i in items)

    # Reaching the publication time must still allow this headline through:
    # rejecting it earlier must not have consumed its dedup entry.
    items, exc = fetch_news(WATCHLIST, seen, now=NOW + timedelta(days=1))
    assert exc == []
    assert any(i.issuer_id == "ACME" for i in items)


def test_dedup_across_runs_returns_no_acme_item_on_second_run(requests_mock, tmp_path):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))
    seen = tmp_path / "seen.json"

    first, _ = fetch_news(WATCHLIST, seen, now=NOW)
    assert any(i.issuer_id == "ACME" for i in first)

    # Second run, same feeds — ACME's title is already in seen.json
    second, _ = fetch_news(WATCHLIST, seen, now=NOW + timedelta(hours=1))
    assert all(i.issuer_id != "ACME" for i in second)


def test_alert_keyword_sets_is_alert(requests_mock, tmp_path):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_alert.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    acme = next(i for i in items if i.issuer_id == "ACME")
    assert acme.is_alert is True
    assert "downgrade" in acme.summary.lower()


@pytest.mark.parametrize("keyword_scope", ["global", "issuer"])
def test_single_alert_keyword_string_is_not_split_into_characters(
    requests_mock, tmp_path, keyword_scope
):
    wl = tmp_path / "wl.yaml"
    settings = (
        "settings:\n  alert_keywords: downgrade\n"
        if keyword_scope == "global"
        else "settings: {}\n"
    )
    issuer_keyword = "    alert_keywords: downgrade\n" if keyword_scope == "issuer" else ""
    wl.write_text(
        settings
        + "issuers:\n"
        + "  - id: ACME\n"
        + f"{issuer_keyword}"
        + f"    rss: [{ACME_RSS_URL}]\n",
        encoding="utf-8",
    )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><item>'
        '<title>ACME opens new distribution center</title>'
        '<link>https://acme.com/center</link>'
        '<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate>'
        '</item></channel></rss>'
    )
    requests_mock.get(ACME_RSS_URL, text=rss)

    items, exc = fetch_news(wl, tmp_path / "seen.json", now=NOW)

    assert exc == []
    assert len(items) == 1
    assert items[0].is_alert is False


def test_max_items_per_issuer_limit(tmp_path, requests_mock):
    # Build a watchlist with max_items=1 and an RSS with 3 fresh items
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - id: ACME\n"
        "    name: ACME\n"
        "    rss:\n"
        "      - https://example.com/acme/press.rss\n"
        "    enabled: true\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n"
        "  dedup_lookback_days: 3\n"
        "  similarity_threshold: 0.85\n"
        "  alert_keywords: []\n",
        encoding="utf-8",
    )
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>ACME headline one</title><link>https://acme.com/1</link><pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>
  <item><title>ACME headline two</title><link>https://acme.com/2</link><pubDate>Sat, 25 Apr 2026 09:30:00 +0000</pubDate></item>
  <item><title>ACME headline three</title><link>https://acme.com/3</link><pubDate>Sat, 25 Apr 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""
    requests_mock.get(ACME_RSS_URL, text=rss)

    items, _ = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert len(items) == 1


def test_watchlist_missing_returns_full_gap(tmp_path):
    items, exc = fetch_news(tmp_path / "no_such_file.yaml", tmp_path / "seen.json", now=NOW)
    assert items is None
    assert exc == ["news: watchlist file not found"]


def test_watchlist_malformed_returns_full_gap(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("settings: {}\n", encoding="utf-8")  # no 'issuers' key
    items, exc = fetch_news(bad, tmp_path / "seen.json", now=NOW)
    assert items is None
    assert exc and "malformed" in exc[0]


@pytest.mark.parametrize("issuers", ["null", "ACME", "{id: ACME}"])
def test_watchlist_rejects_non_list_issuers(tmp_path, issuers):
    bad = tmp_path / "bad.yaml"
    bad.write_text(f"issuers: {issuers}\n", encoding="utf-8")

    items, exc = fetch_news(bad, tmp_path / "seen.json", now=NOW)

    assert items is None
    assert exc == ["news: watchlist malformed ('issuers' must be a list)"]


@pytest.mark.parametrize("settings", ["invalid", "[one, two]"])
def test_watchlist_rejects_non_mapping_settings(tmp_path, settings):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        f"issuers: []\nsettings: {settings}\n",
        encoding="utf-8",
    )

    items, exc = fetch_news(bad, tmp_path / "seen.json", now=NOW)

    assert items is None
    assert exc == ["news: watchlist malformed ('settings' must be a mapping)"]


@pytest.mark.parametrize(
    "setting",
    ["lookback_hours: nope", "similarity_threshold: []"],
)
def test_watchlist_rejects_non_numeric_settings(tmp_path, setting):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        f"issuers: []\nsettings:\n  {setting}\n",
        encoding="utf-8",
    )

    items, exc = fetch_news(bad, tmp_path / "seen.json", now=NOW)

    assert items is None
    assert exc == ["news: watchlist malformed (settings must be numeric)"]


@pytest.mark.parametrize(
    "setting",
    [
        "lookback_hours: 0",
        "max_items_per_issuer: -1",
        "dedup_lookback_days: -1",
        "similarity_threshold: 1.1",
        "similarity_threshold: .nan",
    ],
)
def test_watchlist_rejects_out_of_range_settings(tmp_path, setting):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        f"issuers: []\nsettings:\n  {setting}\n",
        encoding="utf-8",
    )

    items, exc = fetch_news(bad, tmp_path / "seen.json", now=NOW)

    assert items is None
    assert exc == ["news: watchlist malformed (settings out of range)"]


def test_direct_feed_network_error_logged_and_run_does_not_crash(
    monkeypatch, requests_mock, tmp_path
):
    # Direct ACME feed times out; a separately-empty Google News mock catches
    # both ACME's fallback and BETA's primary call. The point is that the run
    # returns a list (not None — None means watchlist-level failure) and
    # ACME's network error is preserved in the exceptions list.
    from src.sources import news as news_mod
    monkeypatch.setattr(news_mod.time, "sleep", lambda _s: None)

    requests_mock.get(ACME_RSS_URL, exc=requests.exceptions.ConnectTimeout)
    requests_mock.get(GN_URL, text='<?xml version="1.0"?><rss><channel></channel></rss>')

    items, exc = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    assert items is not None
    assert any("ACME" in e and "ConnectTimeout" in e for e in exc)


def test_issuer_id_falls_back_to_name(tmp_path, requests_mock):
    """When `id` is omitted, `name` becomes the issuer label so the issuer
    isn't silently skipped (and the digest line still reads `<name>: <title>`)."""
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - name: Pfizer\n"
        "    rss:\n"
        "      - https://example.com/pfizer.rss\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n",
        encoding="utf-8",
    )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        "<item><title>Pfizer reports Q1 earnings</title>"
        "<link>https://pfizer.com/q1</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    requests_mock.get("https://example.com/pfizer.rss", text=rss)

    items, exc = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert exc == []
    assert [i.issuer_id for i in items] == ["Pfizer"]


def test_rss_feeds_accepted_as_alias_for_rss(tmp_path, requests_mock):
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - id: PFE\n"
        "    name: Pfizer\n"
        "    rss_feeds:\n"
        "      - https://example.com/pfizer.rss\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n",
        encoding="utf-8",
    )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        "<item><title>Pfizer headline</title>"
        "<link>https://pfizer.com/1</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    requests_mock.get("https://example.com/pfizer.rss", text=rss)

    items, _ = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert [i.issuer_id for i in items] == ["PFE"]
    # The configured RSS URL was actually hit (not the Google News fallback).
    assert any("pfizer.rss" in r.url for r in requests_mock.request_history)


def test_single_rss_url_string_is_treated_as_one_feed(tmp_path, requests_mock):
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - id: ACME\n"
        "    rss: https://example.com/acme/press.rss\n",
        encoding="utf-8",
    )
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))

    items, exc = fetch_news(wl, tmp_path / "seen.json", now=NOW)

    assert exc == []
    assert [item.issuer_id for item in items] == ["ACME"]
    assert len(requests_mock.request_history) == 1


def test_query_accepted_as_alias_for_google_news_query(tmp_path, requests_mock):
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - id: NSL\n"
        "    name: 南山人壽\n"
        "    query: 南山人壽 bond credit rating\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n",
        encoding="utf-8",
    )
    requests_mock.get(GN_URL, text='<?xml version="1.0"?><rss><channel></channel></rss>')

    fetch_news(wl, tmp_path / "seen.json", now=NOW)
    gn_calls = [r for r in requests_mock.request_history if GN_URL in r.url]
    assert len(gn_calls) == 1
    # ASCII portions of the query survive URL-encoding intact; check those.
    assert "bond" in gn_calls[0].url and "credit" in gn_calls[0].url


def test_top_level_lookback_hours_honored_when_settings_lacks_it(tmp_path, requests_mock):
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "lookback_hours: 1\n"  # 1h window; the RSS item below is 6h old
        "issuers:\n"
        "  - id: ACME\n"
        "    name: ACME\n"
        "    rss:\n"
        "      - https://example.com/acme/press.rss\n"
        "settings:\n"
        "  max_items_per_issuer: 1\n",
        encoding="utf-8",
    )
    six_hours_ago = (NOW - timedelta(hours=6)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<rss version=\"2.0\"><channel>"
        f"<item><title>Old ACME news</title>"
        f"<link>https://acme.com/1</link>"
        f"<pubDate>{six_hours_ago}</pubDate></item>"
        f"</channel></rss>"
    )
    requests_mock.get(ACME_RSS_URL, text=rss)
    requests_mock.get(GN_URL, text='<?xml version="1.0"?><rss><channel></channel></rss>')

    items, _ = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert items == []  # filtered out by the top-level 1h lookback


def test_per_issuer_alert_keywords_combine_with_global_list(tmp_path, requests_mock):
    """A title hitting *only* a per-issuer keyword still alerts, and a title
    hitting *only* the global list still alerts."""
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "issuers:\n"
        "  - id: NSL\n"
        "    name: 南山人壽\n"
        "    rss:\n"
        "      - https://example.com/nsl.rss\n"
        "    alert_keywords:\n"
        "      - 降評\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n"
        "  alert_keywords:\n"
        "    - downgrade\n",
        encoding="utf-8",
    )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        "<item><title>南山人壽遭穆迪降評</title>"
        "<link>https://x/1</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    requests_mock.get("https://example.com/nsl.rss", text=rss)

    items, _ = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert len(items) == 1
    assert items[0].is_alert is True


def test_user_schema_does_not_silently_skip_any_issuer(tmp_path, requests_mock):
    """Regression for the schema-mismatch bug: a watchlist using the richer
    schema (no `id`, `rss_feeds`, `query`, top-level `lookback_hours`,
    per-issuer `alert_keywords`) must reach Google News for every enabled
    issuer instead of being silently skipped."""
    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "lookback_hours: 24\n"
        "issuers:\n"
        "  - name: 南山人壽\n"
        "    query: 南山人壽 bond credit rating\n"
        "    rss_feeds: []\n"
        "    alert_keywords: [降評]\n"
        "  - name: Pfizer\n"
        "    query: Pfizer bond credit rating\n"
        "    rss_feeds: []\n"
        "    alert_keywords: [downgrade]\n"
        "  - name: Google\n"
        "    query: Alphabet Google bond\n"
        "    rss_feeds: []\n"
        "settings:\n"
        "  max_items_per_issuer: 1\n",
        encoding="utf-8",
    )
    requests_mock.get(GN_URL, text='<?xml version="1.0"?><rss><channel></channel></rss>')

    items, exc = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    assert exc == []
    assert items == []  # empty mock RSS — but the point is the *attempts*
    gn_calls = [r for r in requests_mock.request_history if GN_URL in r.url]
    assert len(gn_calls) == 3  # one per issuer


def test_direct_feeds_are_fetched_in_parallel(monkeypatch, tmp_path):
    """Four issuers × one slow feed: sequential total would be ~0.8s (4 ×
    0.2s); the parallel implementation should finish in roughly the slowest
    single fetch plus thread-pool overhead. Patches _fetch_entries directly
    so the timing reflects the orchestrator's parallelism (requests_mock
    serializes its dispatcher under a lock, defeating thread-level timing
    tests through the HTTP layer)."""
    import time as _time
    from src.sources import news as news_mod

    delay = 0.2
    fresh_pubdate = (NOW - timedelta(hours=1)).utctimetuple()
    # Titles must be sufficiently dissimilar (the dedup similarity threshold
    # defaults to 0.85) — otherwise the first item poisons the seen list and
    # the others look like near-duplicates of it.
    titles = {
        "https://example.com/a.rss": "Alpha Corp Q1 earnings beat estimates",
        "https://example.com/b.rss": "Beta Industries announces dividend hike",
        "https://example.com/c.rss": "Gamma Networks files patent dispute lawsuit",
        "https://example.com/d.rss": "Delta Holdings restructures debt portfolio",
    }

    def slow_fetch(url: str, timeout: float):
        _time.sleep(delay)
        return [{
            "title": titles[url],
            "link": url,
            "published_parsed": fresh_pubdate,
        }]

    monkeypatch.setattr(news_mod, "_fetch_entries", slow_fetch)

    wl = tmp_path / "wl.yaml"
    wl.write_text(
        "settings:\n  lookback_hours: 24\n  max_items_per_issuer: 1\n"
        "issuers:\n"
        "  - id: A\n    rss: [https://example.com/a.rss]\n"
        "  - id: B\n    rss: [https://example.com/b.rss]\n"
        "  - id: C\n    rss: [https://example.com/c.rss]\n"
        "  - id: D\n    rss: [https://example.com/d.rss]\n",
        encoding="utf-8",
    )

    t0 = _time.monotonic()
    items, _ = fetch_news(wl, tmp_path / "seen.json", now=NOW)
    elapsed = _time.monotonic() - t0

    assert elapsed < 0.5, (
        f"4 × {delay}s fetches took {elapsed:.2f}s — looks sequential "
        f"(would be ~{4 * delay}s)"
    )
    assert {i.issuer_id for i in items} == {"A", "B", "C", "D"}


def test_persist_seen_false_does_not_write_seen_file_and_yields_repeatable_items(
    requests_mock, tmp_path
):
    """A preview run (persist_seen=False) must not consume dedup state — the
    same items should re-emerge on a second call against the same seen path.
    Otherwise --dry-run silently steals headlines from the next --push."""
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))
    seen = tmp_path / "seen.json"

    first, _ = fetch_news(WATCHLIST, seen, now=NOW, persist_seen=False)
    assert any(i.issuer_id == "ACME" for i in first)
    assert not seen.exists(), "preview run wrote seen.json"

    second, _ = fetch_news(
        WATCHLIST, seen, now=NOW + timedelta(hours=1), persist_seen=False
    )
    assert any(i.issuer_id == "ACME" for i in second), (
        "ACME item disappeared on second preview — preview is mutating dedup state"
    )


def test_per_issuer_fetch_summary_is_logged(requests_mock, tmp_path, caplog):
    """Each enabled issuer emits one INFO summary line (candidates/chosen/
    stale/dup) so 'why no news for X today?' is answerable from cron logs.
    Disabled issuers (GAMMA) don't appear."""
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    with caplog.at_level(logging.INFO, logger="src.sources.news"):
        fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)

    summaries = [r.getMessage() for r in caplog.records if "candidates=" in r.getMessage()]
    assert any("news: ACME:" in s for s in summaries)
    assert any("news: BETA:" in s for s in summaries)
    assert all("GAMMA" not in s for s in summaries)
    # ACME has one fresh, unique item, so it should report shown=1.
    acme = next(s for s in summaries if "news: ACME:" in s)
    assert "shown=1" in acme


def test_displayed_alert_logs_matched_keyword(requests_mock, tmp_path, caplog):
    """When an alert headline is displayed, the matched keyword is logged so
    noisy/over-broad keywords (e.g. 'debt' tripping on 'less net debt') are
    identifiable from cron logs without re-running the digest manually."""
    requests_mock.get(ACME_RSS_URL, text=_read("rss_alert.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    with caplog.at_level(logging.INFO, logger="src.sources.news"):
        fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)

    alert_lines = [
        r.getMessage() for r in caplog.records if "ALERT shown" in r.getMessage()
    ]
    assert len(alert_lines) >= 1, "no ALERT log line emitted for the downgrade item"
    acme_line = next(s for s in alert_lines if "news: ACME:" in s)
    assert "keyword: downgrade" in acme_line
    # The headline text is in the line (truncated to 120 chars), repr-quoted.
    assert "downgrade" in acme_line.lower()


def test_alert_keyword_in_non_top_headline_still_fires_and_is_shown(
    requests_mock, tmp_path
):
    """Regression: with max_items_per_issuer=1 the old code only checked the
    first fresh item, so a risk headline ranked below it never tripped the
    alert. Now every eligible headline is scanned, and the alert one is
    promoted into the single display slot."""
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        # Benign item first (most recent by feed order)...
        "<item><title>ACME opens new distribution center</title>"
        "<link>https://acme.com/dc</link>"
        "<pubDate>Sat, 25 Apr 2026 11:00:00 +0000</pubDate></item>"
        # ...risk item second — must still trip the alert and be shown.
        "<item><title>ACME debt downgrade by S&amp;P</title>"
        "<link>https://acme.com/downgrade</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    requests_mock.get(ACME_RSS_URL, text=rss)
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    acme = [i for i in items if i.issuer_id == "ACME"]
    # Cap respected: still only one ACME item shown...
    assert len(acme) == 1
    # ...and it's the downgrade headline, flagged as an alert.
    assert acme[0].is_alert is True
    assert "downgrade" in acme[0].summary.lower()


def test_no_alert_keyword_shows_first_by_feed_order(requests_mock, tmp_path):
    """No risk keyword anywhere → behavior unchanged: the first-by-feed-order
    fresh item is shown, not flagged."""
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        "<item><title>ACME opens new distribution center</title>"
        "<link>https://acme.com/dc</link>"
        "<pubDate>Sat, 25 Apr 2026 11:00:00 +0000</pubDate></item>"
        "<item><title>ACME hires new head of marketing</title>"
        "<link>https://acme.com/cmo</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    requests_mock.get(ACME_RSS_URL, text=rss)
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    acme = [i for i in items if i.issuer_id == "ACME"]
    assert len(acme) == 1
    assert acme[0].is_alert is False
    assert acme[0].summary == "ACME opens new distribution center"


def test_fetch_entries_retries_once_on_transient_failure(monkeypatch, requests_mock):
    """A single transient blip (connection reset, 5xx, etc.) shouldn't surface
    as an exception line — _fetch_entries retries once before giving up."""
    from src.sources import news as news_mod

    # Skip the real sleep so the test stays fast.
    monkeypatch.setattr(news_mod.time, "sleep", lambda _s: None)

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        "<item><title>second try works</title><link>https://x/1</link>"
        "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    )
    # requests_mock takes a list of response specs to give different responses
    # for successive calls to the same URL.
    requests_mock.get(
        "https://example.com/feed.rss",
        [
            {"exc": requests.exceptions.ConnectionError},
            {"text": rss, "status_code": 200},
        ],
    )

    entries = news_mod._fetch_entries("https://example.com/feed.rss", timeout=1.0)
    assert len(entries) == 1
    assert entries[0]["title"] == "second try works"


def test_fetch_entries_propagates_last_error_after_exhausting_retries(
    monkeypatch, requests_mock
):
    """If every attempt fails, the last RequestException must propagate so the
    caller (_fetch_all) can attribute the failure to the right issuer."""
    from src.sources import news as news_mod

    monkeypatch.setattr(news_mod.time, "sleep", lambda _s: None)

    requests_mock.get(
        "https://example.com/feed.rss",
        exc=requests.exceptions.ConnectTimeout,
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        news_mod._fetch_entries("https://example.com/feed.rss", timeout=1.0)


def test_news_fetch_sends_user_agent_header(requests_mock, tmp_path):
    """SEC EDGAR rejects the default `python-requests` UA with 403, so every
    feed fetch must carry a non-default User-Agent."""
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)

    assert requests_mock.request_history, "no HTTP requests recorded"
    for r in requests_mock.request_history:
        ua = r.headers.get("User-Agent", "")
        assert ua and not ua.startswith("python-requests"), (
            f"request to {r.url} sent unsuitable UA: {ua!r}"
        )


def test_news_item_captures_link_from_rss_entry(requests_mock, tmp_path):
    """RSS `<link>` round-trips into NewsItem.link so the formatter can render
    a tappable URL alongside the headline in LINE messages."""
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))

    items, _ = fetch_news(WATCHLIST, tmp_path / "seen.json", now=NOW)
    acme = next(i for i in items if i.issuer_id == "ACME")
    assert acme.link and acme.link.startswith("http")


def test_3day_rolloff_lets_old_dedup_entry_expire_so_item_re_emerges(tmp_path, requests_mock):
    requests_mock.get(ACME_RSS_URL, text=_read("rss_acme.xml"))
    requests_mock.get(GN_URL, text=_read("rss_google_beta.xml"))
    seen_path = tmp_path / "seen.json"
    # Pre-seed with an entry from 4 days ago that matches ACME's headline
    save_seen(
        seen_path,
        [SeenEntry(
            title_norm="acme q1 results in line with guidance",
            first_seen=(NOW - timedelta(days=4)).isoformat(),
        )],
    )
    items, _ = fetch_news(WATCHLIST, seen_path, now=NOW)
    # The stale dedup entry should have been pruned, so ACME's item re-emerges
    assert any(i.issuer_id == "ACME" for i in items)
