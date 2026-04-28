from datetime import date
from decimal import Decimal
from pathlib import Path

from src.sources.ledger import load_ledger

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 4, 25)


def test_basic_today_mtd_bal():
    cf, exc = load_ledger(FIXTURES / "ledger_basic.csv", today=TODAY)
    assert exc == []
    assert cf is not None
    # Today: USD coupon today; CHF custody fee today
    assert cf.today["USD"] == Decimal("55.00")
    assert cf.today["CHF"] == Decimal("-12.50")
    # MTD: April 1 + April 15 + April 25 in USD; April 22 + April 25 in CHF
    assert cf.mtd["USD"] == Decimal("610.00")
    assert cf.mtd["CHF"] == Decimal("197.50")
    # Bal: includes March 15 USD prior-month row
    assert cf.bal["USD"] == Decimal("1610.00")
    assert cf.bal["CHF"] == Decimal("197.50")


def test_future_dated_row_excluded_from_all_buckets():
    cf, _ = load_ledger(FIXTURES / "ledger_basic.csv", today=TODAY)
    # The 2026-04-26 row in the fixture is -3.00 USD — must not appear anywhere.
    assert cf.bal["USD"] == Decimal("1610.00")
    assert cf.today["USD"] == Decimal("55.00")


def test_bad_rows_skipped_with_exceptions():
    cf, exc = load_ledger(FIXTURES / "ledger_bad_rows.csv", today=TODAY)
    assert cf is not None
    # Two valid USD rows on today: 55 + 20
    assert cf.today["USD"] == Decimal("75.00")
    assert cf.today["CHF"] == Decimal("0.00")
    # Three rejections logged (bad date, bad amount, JPY currency)
    joined = " | ".join(exc)
    assert "bad date" in joined
    assert "bad amount" in joined
    assert "JPY" in joined
    assert len(exc) == 3


def test_missing_file_returns_zero_cashflow_no_exception():
    """The ledger tracks actuals and is intentionally optional. A missing file
    is a clean opt-out, not a broken state — render zeros and don't add a
    Data-gap / exception line that would clutter the daily digest."""
    cf, exc = load_ledger(FIXTURES / "does_not_exist.csv", today=TODAY)
    assert cf is not None
    assert exc == []
    assert cf.today["USD"] == cf.today["CHF"] == Decimal("0.00")
    assert cf.mtd["USD"] == cf.mtd["CHF"] == Decimal("0.00")
    assert cf.bal["USD"] == cf.bal["CHF"] == Decimal("0.00")


def test_empty_file_returns_gap(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    cf, exc = load_ledger(p, today=TODAY)
    assert cf is None
    assert exc == ["ledger: empty file"]


def test_header_only_returns_zero_cashflow():
    cf, exc = load_ledger(FIXTURES / "ledger_header_only.csv", today=TODAY)
    assert cf is not None
    assert exc == []
    assert cf.today["USD"] == cf.today["CHF"] == Decimal("0.00")
    assert cf.bal["USD"] == cf.bal["CHF"] == Decimal("0.00")


def test_wrong_header_returns_gap():
    cf, exc = load_ledger(FIXTURES / "ledger_wrong_header.csv", today=TODAY)
    assert cf is None
    assert exc and exc[0].startswith("ledger: unexpected header")


def test_row_on_first_of_month_counts_in_mtd_not_today():
    cf, _ = load_ledger(FIXTURES / "ledger_basic.csv", today=TODAY)
    # 2026-04-01 USD 500.00 is in MTD but not today
    assert Decimal("500.00") <= cf.mtd["USD"]
    assert cf.today["USD"] == Decimal("55.00")


def test_too_few_columns_logs_exception_and_keeps_valid_rows(tmp_path):
    p = tmp_path / "short_row.csv"
    p.write_text(
        "date,amount,currency,category,description\n"
        "2026-04-25,55.00\n"
        "2026-04-25,20.00,USD,coupon,ok\n",
        encoding="utf-8",
    )
    cf, exc = load_ledger(p, today=TODAY)
    assert cf is not None
    assert cf.today["USD"] == Decimal("20.00")
    assert exc == ["ledger row 2: too few columns"]


def test_non_finite_amount_rejected_with_exception(tmp_path):
    # Decimal accepts "Infinity" and "NaN" — these must not flow into sums.
    p = tmp_path / "nonfinite.csv"
    p.write_text(
        "date,amount,currency,category,description\n"
        "2026-04-25,Infinity,USD,deposit,bogus\n"
        "2026-04-25,NaN,USD,deposit,also bogus\n"
        "2026-04-25,55.00,USD,coupon,ok\n",
        encoding="utf-8",
    )
    cf, exc = load_ledger(p, today=TODAY)
    assert cf is not None
    assert cf.today["USD"] == Decimal("55.00")
    assert sum("non-finite" in e for e in exc) == 2
