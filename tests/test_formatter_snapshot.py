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
            NewsItem("ACME", "Q1 results in line with guidance", "acme.com"),
            NewsItem("BETA", "New CFO appointed", "reuters.com"),
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
            today_usd=Decimal("55.00"),
            today_chf=Decimal("-12.50"),
            mtd_usd=Decimal("1065.00"),
            mtd_chf=Decimal("197.50"),
            bal_usd=Decimal("1065.00"),
            bal_chf=Decimal("197.50"),
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
    assert "⚠️ Status: Alert" in out
    assert "✅ Status: All clear" not in out


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
    assert "- Next Coupon (7d): Issuer X 2026-05-01 100.00 CHF" in out
