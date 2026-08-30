"""Live-quote collector tests.

The two HTML fixtures are trimmed captures of the real pages, not markup
written to satisfy the parser — a regex that only works against something
we invented would prove nothing about the sites we actually scrape.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

import pytest
import requests

from src.sources import quotes as quotes_mod
from src.sources.quotes import ESUN_URL, PUBLIC_URL, fetch_quotes

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 8, 26)

PUBLIC_DOW = (FIXTURES / "public_bond_dow.html").read_text(encoding="utf-8")
ESUN_TABLE = (FIXTURES / "esun_bond_table.html").read_text(encoding="utf-8")

DOW_URL = PUBLIC_URL.format(cusip="260543by8")
POSITIONS_HEADER = (
    "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
    "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
    "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
)
DOW_ROW = (
    "bond,陶氏化學 DOW 9.4% 2039,US260543BY86,,131000,9.40,2039-05-15,"
    "132.7100,173850.10,12314.00,6157.00,5.82,7.0831,5/15;11/15\n"
)
NANSHAN_ROW = (
    "insurance,南山人壽(10年期),XS2888260564,,1444000,5.45,2034-09-11,"
    "97.6800,1410499.00,78698.00,39349.00,5.45,5.5794,3/11;9/11\n"
)
FCN_ROW = (
    "note,FCN [MSFT+TSM] 12.20%,CH1550441922,20260601,100000,12.20,2026-10-08,"
    "100.0000,100000.00,4067.00,,12.20,12.20,\n"
)


def _positions(tmp_path: Path, *rows: str) -> Path:
    p = tmp_path / "positions.csv"
    p.write_text(POSITIONS_HEADER + "".join(rows), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(quotes_mod.time, "sleep", lambda _s: None)


def test_public_com_quote_is_parsed_from_the_real_page_markup(tmp_path, requests_mock):
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert exc == []
    assert got["US260543BY86"].price == Decimal("130.69")
    # The page carries no timestamp, so the run date is the honest as-of.
    assert got["US260543BY86"].as_of == TODAY


def test_isin_is_converted_to_the_cusip_in_the_url(tmp_path, requests_mock):
    """US260543BY86 -> 260543by8: drop the country prefix and check digit.
    Getting this wrong fetches someone else's bond, so it is worth pinning."""
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert requests_mock.request_history[0].url == DOW_URL


def test_esun_quote_uses_the_redemption_price_and_its_own_date(tmp_path, requests_mock):
    """The table's row date beats assuming today — it does not move at
    weekends. And the redemption side is what the position is worth to us,
    not what buying more would cost (98.35 vs 95.56 on this row)."""
    requests_mock.get(ESUN_URL, text=ESUN_TABLE)
    got, exc = fetch_quotes(_positions(tmp_path, NANSHAN_ROW), TODAY)
    assert exc == []
    assert got["XS2888260564"].price == Decimal("95.56")
    assert got["XS2888260564"].as_of == date(2026, 8, 26)


@pytest.mark.parametrize(
    ("source_date", "reason"),
    [
        ("", "quote date unavailable"),
        ("(2026/08/27)", "quote date 2026-08-27 is in the future"),
    ],
)
def test_esun_quote_without_a_trustworthy_date_is_rejected(
    tmp_path, requests_mock, source_date, reason
):
    """An undated or future-dated scrape must not replace a dated fallback.

    Price freshness is part of the quote's integrity: a partial page change
    can leave the number parseable while dropping its date, and a future date
    indicates bad source data or a parsing mistake.
    """
    html = ESUN_TABLE.replace("(2026/08/26)", source_date)
    requests_mock.get(ESUN_URL, text=html)
    got, exc = fetch_quotes(_positions(tmp_path, NANSHAN_ROW), TODAY)
    assert got == {}
    assert exc == [f"quotes XS2888260564: {reason}"]


def test_instruments_without_a_public_quote_are_never_requested(tmp_path, requests_mock):
    """The FCN's CH code has no source to look up; going out for it would
    only produce a daily exception line about a bond that cannot be quoted."""
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW, FCN_ROW), TODAY)
    assert set(got) == {"US260543BY86"}
    assert exc == []
    assert len(requests_mock.request_history) == 1


@pytest.mark.parametrize("bad_isin", ["US260543BY8", "US260543BY85"])
def test_invalid_isin_is_reported_before_any_network_request(
    tmp_path, requests_mock, bad_isin
):
    """A US/XS prefix alone does not make a trustworthy lookup target.

    The first case has the wrong length; the second has a bad ISO 6166 Luhn
    check digit. Neither should be converted into a CUSIP and sent upstream.
    """
    row = DOW_ROW.replace("US260543BY86", bad_isin)
    got, exc = fetch_quotes(_positions(tmp_path, row), TODAY)
    assert got == {}
    assert exc == [f"quotes row 2: invalid ISIN {bad_isin!r}"]
    assert requests_mock.request_history == []


def test_duplicate_lots_fetch_their_shared_isin_only_once(tmp_path, requests_mock):
    """Multiple lots of one bond share one market quote.

    Besides wasting a request, fetching twice used to let a failure on the
    second lot remove a quote already obtained for the first.
    """
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW, DOW_ROW), TODAY)
    assert exc == []
    assert got["US260543BY86"].price == Decimal("130.69")
    assert len(requests_mock.request_history) == 1


def test_conflicting_duplicate_isin_is_rejected_before_lookup(tmp_path, requests_mock):
    """Two rows cannot safely identify one bond when their terms disagree."""
    conflicting = DOW_ROW.replace("9.40,2039-05-15", "4.10,2041-05-15")
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW, conflicting), TODAY)
    assert got == {}
    assert exc == [
        "quotes row 3: conflicting duplicate ISIN 'US260543BY86'"
    ]
    assert requests_mock.request_history == []


def test_wrong_bond_on_the_page_is_rejected_rather_than_trusted(tmp_path, requests_mock):
    """If the URL ever resolves to a different security, coupon and maturity
    stop matching. No quote is the right answer — a plausible wrong price
    would silently misprice the book."""
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    wrong = DOW_ROW.replace("9.40,2039-05-15", "4.10,2039-05-15")
    got, exc = fetch_quotes(_positions(tmp_path, wrong), TODAY)
    assert got == {}
    assert exc == [
        "quotes US260543BY86: bond identity check failed (coupon 9.40 != 4.10)"
    ]


def test_maturity_mismatch_is_also_rejected(tmp_path, requests_mock):
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    wrong = DOW_ROW.replace("9.40,2039-05-15", "9.40,2041-05-15")
    got, exc = fetch_quotes(_positions(tmp_path, wrong), TODAY)
    assert got == {}
    assert exc == [
        "quotes US260543BY86: bond identity check failed "
        "(maturity 2039-05-15 != 2041-05-15)"
    ]


@pytest.mark.parametrize(
    ("missing_label", "reason"),
    [("Coupon", "coupon unavailable"), ("Maturity", "maturity unavailable")],
)
def test_missing_public_identity_field_rejects_an_otherwise_parseable_quote(
    tmp_path, requests_mock, missing_label, reason
):
    """A price alone is insufficient when the holding supplies identity data.

    This catches partial page redesigns where the price selector still works
    but one of the fields used to prove that the page is for this bond does
    not. The stored quote should win instead of an unverified live value.
    """
    html = re.sub(
        rf'<p class="[^"]*__label">{missing_label}</p>.*?__value"[^>]*>[^<]*<',
        "",
        PUBLIC_DOW,
        count=1,
        flags=re.S,
    )
    requests_mock.get(DOW_URL, text=html)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert got == {}
    assert exc == [
        f"quotes US260543BY86: bond identity check failed ({reason})"
    ]


def test_price_outside_the_plausible_band_is_rejected(tmp_path, requests_mock):
    """A parse that latches onto a chart coordinate or a dollar amount lands
    far outside any bond's clean price."""
    requests_mock.get(DOW_URL, text=PUBLIC_DOW.replace("$130.69", "$13069.00"))
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert got == {}
    assert exc == ["quotes US260543BY86: price 13069.00 out of range"]


def test_redesigned_page_yields_no_quote_not_a_wrong_one(tmp_path, requests_mock):
    requests_mock.get(DOW_URL, text="<html><body>redesigned</body></html>")
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert got == {}
    assert exc == ["quotes US260543BY86: price not found on public.com"]


def test_one_failed_holding_does_not_cost_the_others(tmp_path, requests_mock):
    requests_mock.get(DOW_URL, status_code=503)
    requests_mock.get(ESUN_URL, text=ESUN_TABLE)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW, NANSHAN_ROW), TODAY)
    assert set(got) == {"XS2888260564"}
    assert exc == ["quotes US260543BY86: HTTPError"]


def test_source_outage_is_reported_and_returns_nothing(tmp_path, requests_mock):
    requests_mock.get(DOW_URL, exc=requests.exceptions.ConnectTimeout)
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert got == {}
    assert exc == ["quotes US260543BY86: ConnectTimeout"]


def test_bond_missing_from_the_esun_table_is_reported(tmp_path, requests_mock):
    requests_mock.get(ESUN_URL, text="<html><body><table></table></body></html>")
    got, exc = fetch_quotes(_positions(tmp_path, NANSHAN_ROW), TODAY)
    assert got == {}
    assert exc == ["quotes XS2888260564: not listed by esunbank"]


def test_missing_positions_file_is_reported_without_any_request(tmp_path, requests_mock):
    got, exc = fetch_quotes(tmp_path / "nope.csv", TODAY)
    assert got == {}
    assert exc == ["quotes: positions file not found"]
    assert requests_mock.request_history == []


def test_transient_failure_is_retried_once(tmp_path, requests_mock):
    requests_mock.get(
        DOW_URL,
        [{"exc": requests.exceptions.ConnectTimeout}, {"text": PUBLIC_DOW}],
    )
    got, exc = fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert exc == []
    assert got["US260543BY86"].price == Decimal("130.69")


def test_public_com_is_sent_a_browser_user_agent(tmp_path, requests_mock):
    """The default python-requests UA gets blocked."""
    requests_mock.get(DOW_URL, text=PUBLIC_DOW)
    fetch_quotes(_positions(tmp_path, DOW_ROW), TODAY)
    assert "Mozilla/5.0" in requests_mock.request_history[0].headers["User-Agent"]
