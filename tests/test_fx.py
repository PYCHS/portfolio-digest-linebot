from datetime import date

from decimal import Decimal

import pytest
import requests

from src.sources import fx as fx_mod
from src.sources.fx import fetch_fx

LATEST = "https://api.frankfurter.app/latest"
PRIOR = "https://api.frankfurter.app/2026-04-24"


def test_happy_path_full_result(requests_mock):
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    requests_mock.get(PRIOR, json={"date": "2026-04-24", "rates": {"CHF": 0.9134}})
    fx, exc = fetch_fx()
    assert exc == []
    assert fx is not None
    assert fx.usd_chf == Decimal("0.9123")
    assert fx.chf_usd == Decimal("1.0961")
    # (0.9123 - 0.9134) / 0.9134 * 100 = -0.12042... → -0.12
    assert fx.usd_chf_dod_pct == Decimal("-0.12")
    # The rate's as-of date is carried through so the formatter can flag
    # staleness when the digest fires before Frankfurter publishes today's
    # rate.
    assert fx.as_of == date(2026, 4, 25)


def test_reciprocal_is_4dp_half_up(requests_mock):
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.8000}})
    requests_mock.get(PRIOR, json={"date": "2026-04-24", "rates": {"CHF": 0.8000}})
    fx, _ = fetch_fx()
    # 1 / 0.8 = 1.25 exactly
    assert fx.chf_usd == Decimal("1.2500")


def test_dod_pct_positive_carries_sign(requests_mock):
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9200}})
    requests_mock.get(PRIOR, json={"date": "2026-04-24", "rates": {"CHF": 0.9100}})
    fx, _ = fetch_fx()
    # (0.92 - 0.91) / 0.91 * 100 = 1.0989... → 1.10
    assert fx.usd_chf_dod_pct == Decimal("1.10")


def test_prior_day_returns_same_date_renders_dod_none(requests_mock):
    # Frankfurter served same date for the prior-day request (e.g., long holiday)
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    requests_mock.get(PRIOR, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    fx, exc = fetch_fx()
    assert exc == []
    assert fx is not None
    assert fx.usd_chf == Decimal("0.9123")
    assert fx.usd_chf_dod_pct is None


def test_prior_day_network_error_preserves_today_rate(monkeypatch, requests_mock):
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    requests_mock.get(PRIOR, exc=requests.exceptions.ConnectTimeout)
    fx, exc = fetch_fx()
    assert exc == []  # graceful: prior-day failure does not flag the source
    assert fx is not None
    assert fx.usd_chf == Decimal("0.9123")
    assert fx.usd_chf_dod_pct is None


def test_today_500_returns_full_gap(monkeypatch, requests_mock):
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(LATEST, status_code=500)
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and exc[0].startswith("fx: latest fetch failed")


def test_today_malformed_json_returns_full_gap(requests_mock):
    requests_mock.get(LATEST, text="not json")
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and "fx: latest fetch failed" in exc[0]


def test_today_missing_rate_key_returns_full_gap(requests_mock):
    requests_mock.get(LATEST, json={"date": "2026-04-25"})  # no "rates"
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and "fx: latest fetch failed" in exc[0]


def test_connection_error_on_today_returns_full_gap(monkeypatch, requests_mock):
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(LATEST, exc=requests.exceptions.ConnectionError)
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and exc[0].startswith("fx: latest fetch failed")


def test_non_numeric_rate_returns_full_gap(requests_mock):
    # A non-numeric rate would raise InvalidOperation (not ValueError) from
    # Decimal — make sure we treat it as a clean gap rather than crashing.
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": "N/A"}})
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and "InvalidOperation" in exc[0]


def test_non_finite_rate_returns_full_gap(requests_mock):
    # Decimal accepts "Infinity" — we must reject it before it pollutes math.
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": "Infinity"}})
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and "ValueError" in exc[0]


@pytest.mark.parametrize("rate", [0, -0.5])
def test_non_positive_rate_returns_full_gap(requests_mock, rate):
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": rate}})

    fx, exc = fetch_fx()

    assert fx is None
    assert exc == ["fx: latest fetch failed: ValueError"]


def test_fetch_sends_user_agent_header(requests_mock):
    """Frankfurter doesn't 403 the default UA today, but every other HTTP
    call in the project identifies itself; keep FX consistent so a future
    UA policy doesn't silently blank the FX section."""
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    requests_mock.get(PRIOR, json={"date": "2026-04-24", "rates": {"CHF": 0.9134}})
    fetch_fx()
    assert requests_mock.request_history
    for r in requests_mock.request_history:
        ua = r.headers.get("User-Agent", "")
        assert ua and not ua.startswith("python-requests"), f"bad UA: {ua!r}"


def test_latest_fetch_retries_once_on_transient_failure(monkeypatch, requests_mock):
    """A single transient blip on the latest-rate call shouldn't blank the FX
    section — _fetch retries once before giving up."""
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(
        LATEST,
        [
            {"exc": requests.exceptions.ConnectionError},
            {"json": {"date": "2026-04-25", "rates": {"CHF": 0.9123}}},
        ],
    )
    requests_mock.get(PRIOR, json={"date": "2026-04-24", "rates": {"CHF": 0.9134}})
    fx, exc = fetch_fx()
    assert exc == []
    assert fx is not None
    assert fx.usd_chf == Decimal("0.9123")


def test_latest_fetch_persistent_failure_returns_full_gap(monkeypatch, requests_mock):
    """If every attempt fails, the FX source reports a clean gap (rc handled
    upstream) rather than crashing."""
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(LATEST, exc=requests.exceptions.ConnectTimeout)
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and exc[0].startswith("fx: latest fetch failed")


def test_data_error_is_not_retried(monkeypatch, requests_mock):
    """A 200 response with unusable data must not trigger a retry — it won't
    fix itself, and retrying wastes a request + backoff. Verified by counting
    how many times the latest endpoint is hit."""
    monkeypatch.setattr(fx_mod.time, "sleep", lambda _s: None)
    requests_mock.get(LATEST, json={"date": "2026-04-25"})  # missing "rates"
    fx, exc = fetch_fx()
    assert fx is None
    latest_hits = [r for r in requests_mock.request_history if r.path == "/latest"]
    assert len(latest_hits) == 1, f"expected no retry on data error, got {len(latest_hits)}"
