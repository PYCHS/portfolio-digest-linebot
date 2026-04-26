from decimal import Decimal

import requests

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


def test_prior_day_network_error_preserves_today_rate(requests_mock):
    requests_mock.get(LATEST, json={"date": "2026-04-25", "rates": {"CHF": 0.9123}})
    requests_mock.get(PRIOR, exc=requests.exceptions.ConnectTimeout)
    fx, exc = fetch_fx()
    assert exc == []  # graceful: prior-day failure does not flag the source
    assert fx is not None
    assert fx.usd_chf == Decimal("0.9123")
    assert fx.usd_chf_dod_pct is None


def test_today_500_returns_full_gap(requests_mock):
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


def test_connection_error_on_today_returns_full_gap(requests_mock):
    requests_mock.get(LATEST, exc=requests.exceptions.ConnectionError)
    fx, exc = fetch_fx()
    assert fx is None
    assert exc and exc[0].startswith("fx: latest fetch failed")
