from datetime import date
from decimal import Decimal
from pathlib import Path

from src.formatter import render
from src.models import (
    Cashflow,
    Coupon,
    DigestInput,
    FxResult,
    NewsItem,
    Snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _all_populated() -> DigestInput:
    return DigestInput(
        date_str="2026-04-25",
        news=[
            NewsItem(
                "ACME",
                "Q1 results in line with guidance",
                "acme.com",
                link="https://acme.com/q1",
            ),
            NewsItem(
                "BETA",
                "New CFO appointed",
                "reuters.com",
                link="https://reuters.com/beta-cfo",
            ),
        ],
        fx=FxResult(
            usd_chf=Decimal("0.9123"),
            chf_usd=Decimal("1.0961"),
            usd_chf_dod_pct=Decimal("-0.12"),
        ),
        snapshot=Snapshot(
            total_cost={"USD": Decimal("14750.00")},
            annual_coupon={"USD": Decimal("760.00")},
            next_coupon=Coupon(
                issuer="ACME",
                date=date(2026, 5, 1),
                amount=Decimal("27.50"),
                currency="USD",
            ),
            uncosted_issuers=[],
        ),
        cashflow=Cashflow(
            today={"USD": Decimal("55.00"), "CHF": Decimal("-12.50")},
            mtd={"USD": Decimal("1065.00"), "CHF": Decimal("197.50")},
            bal={"USD": Decimal("1065.00"), "CHF": Decimal("197.50")},
        ),
        exceptions=[],
    )


def _all_gaps() -> DigestInput:
    return DigestInput(
        date_str="2026-04-25",
        news=None,
        fx=None,
        snapshot=None,
        cashflow=None,
        exceptions=["ledger_csv_parse_error: 2 rows skipped"],
    )


def test_render_all_populated_matches_golden():
    expected = (FIXTURES / "expected_all_populated.txt").read_text(encoding="utf-8")
    assert render(_all_populated()) == expected


def test_render_all_gaps_matches_golden():
    expected = (FIXTURES / "expected_all_gaps.txt").read_text(encoding="utf-8")
    assert render(_all_gaps()) == expected


def test_status_flips_to_alert_when_news_item_is_alert():
    d = _all_populated()
    flagged = NewsItem("ACME", "Issuer downgrade by S&P", "reuters.com", is_alert=True)
    d_alert = DigestInput(
        date_str=d.date_str,
        news=[flagged],
        fx=d.fx,
        snapshot=d.snapshot,
        cashflow=d.cashflow,
        exceptions=d.exceptions,
    )
    out = render(d_alert)
    assert "⚠️ Status (狀態): Alert (警報)" in out
    assert "✅ Status (狀態): All clear (一切正常)" not in out


def test_est_annual_coupon_omits_yield_when_cost_currency_is_missing():
    """Yield % is only meaningful when there's a cost basis to divide by.
    A currency that has annual_coupon but no matching total_cost (e.g., a
    portfolio of uncosted FCN positions) must not render '0.00% yield' or
    crash on a div-by-zero — just show the coupon and skip the suffix."""
    base = _all_populated()
    snap_no_cost = Snapshot(
        total_cost={},  # no cost basis at all
        annual_coupon={"USD": Decimal("760.00")},
        next_coupon=base.snapshot.next_coupon,
        uncosted_issuers=["FCN (TSM, BRK)"],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap_no_cost,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "Est. Annual Coupon (預估年息): 760.00 USD" in out
    assert "yield" not in out
    assert "殖利率" not in out


def test_est_annual_coupon_omits_yield_when_total_cost_is_zero():
    """Same guard for explicit-zero cost: divide-by-zero would be misleading
    even if computable (Decimal would raise InvalidOperation)."""
    base = _all_populated()
    snap_zero_cost = Snapshot(
        total_cost={"USD": Decimal("0.00")},
        annual_coupon={"USD": Decimal("760.00")},
        next_coupon=base.snapshot.next_coupon,
        uncosted_issuers=[],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap_zero_cost,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "Est. Annual Coupon (預估年息): 760.00 USD" in out
    assert "yield" not in out


def test_fx_line_appends_as_of_when_rate_is_stale():
    """If the Frankfurter rate is dated to a prior business day (weekend,
    holiday, or pre-CET-noon weekday digest), surface that on the USD/CHF
    line so the reader doesn't think it's today's market move."""
    base = _all_populated()
    stale_fx = FxResult(
        usd_chf=base.fx.usd_chf,
        chf_usd=base.fx.chf_usd,
        usd_chf_dod_pct=base.fx.usd_chf_dod_pct,
        as_of=date(2026, 4, 24),  # date_str is 2026-04-25 → stale by one day
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=stale_fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "USD/CHF: 0.9123 (Δ -0.12% DoD, as of 2026-04-24)" in out


def test_fx_line_omits_as_of_when_rate_matches_today():
    """No staleness annotation when as_of matches the digest's date."""
    base = _all_populated()
    fresh_fx = FxResult(
        usd_chf=base.fx.usd_chf,
        chf_usd=base.fx.chf_usd,
        usd_chf_dod_pct=base.fx.usd_chf_dod_pct,
        as_of=date(2026, 4, 25),  # matches date_str
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=fresh_fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "USD/CHF: 0.9123 (Δ -0.12% DoD)" in out
    assert "as of" not in out


def test_news_item_without_link_renders_without_continuation_line():
    """When link is None/empty, the formatter must not emit an empty
    `  ` continuation line — older fixtures and any source that doesn't
    surface a link should still render cleanly."""
    base = _all_populated()
    no_link = NewsItem("ACME", "Q1 results in line with guidance", "acme.com")
    d = DigestInput(
        date_str=base.date_str,
        news=[no_link],
        fx=base.fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "- ACME: Q1 results in line with guidance (acme.com)" in out
    # No bare-indent continuation line (`  ` followed by nothing meaningful).
    for line in out.splitlines():
        assert not (line.startswith("  ") and line.strip() == "")


def test_next_coupon_renders_non_usd_currency():
    base = _all_populated()
    chf_coupon = Coupon(
        issuer="Issuer X",
        date=date(2026, 5, 1),
        amount=Decimal("100.00"),
        currency="CHF",
    )
    snap = Snapshot(
        total_cost=base.snapshot.total_cost,
        annual_coupon=base.snapshot.annual_coupon,
        next_coupon=chf_coupon,
        uncosted_issuers=[],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert (
        "- Next Coupon (7d) (七日內下個利息): Issuer X 2026-05-01 100.00 CHF"
        in out
    )
