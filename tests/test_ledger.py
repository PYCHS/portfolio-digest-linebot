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
    assert cf.today_usd == Decimal("55.00")
    assert cf.today_chf == Decimal("-12.50")
    # MTD: April 1 + April 15 + April 25 in USD; April 22 + April 25 in CHF
    assert cf.mtd_usd == Decimal("610.00")
    assert cf.mtd_chf == Decimal("197.50")
    # Bal: includes March 15 USD prior-month row
    assert cf.bal_usd == Decimal("1610.00")
    assert cf.bal_chf == Decimal("197.50")


def test_future_dated_row_excluded_from_all_buckets():
    cf, _ = load_ledger(FIXTURES / "ledger_basic.csv", today=TODAY)
    # The 2026-04-26 row in the fixture is -3.00 USD — must not appear anywhere.
    assert cf.bal_usd == Decimal("1610.00")
    assert cf.today_usd == Decimal("55.00")


def test_bad_rows_skipped_with_exceptions():
    cf, exc = load_ledger(FIXTURES / "ledger_bad_rows.csv", today=TODAY)
    assert cf is not None
    # Two valid USD rows on today: 55 + 20
    assert cf.today_usd == Decimal("75.00")
    assert cf.today_chf == Decimal("0.00")
    # Three rejections logged (bad date, bad amount, JPY currency)
    joined = " | ".join(exc)
    assert "bad date" in joined
    assert "bad amount" in joined
    assert "JPY" in joined
    assert len(exc) == 3


def test_missing_file_returns_gap():
    cf, exc = load_ledger(FIXTURES / "does_not_exist.csv", today=TODAY)
    assert cf is None
    assert exc == ["ledger: file not found"]


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
    assert cf.today_usd == cf.today_chf == Decimal("0.00")
    assert cf.bal_usd == cf.bal_chf == Decimal("0.00")


def test_wrong_header_returns_gap():
    cf, exc = load_ledger(FIXTURES / "ledger_wrong_header.csv", today=TODAY)
    assert cf is None
    assert exc and exc[0].startswith("ledger: unexpected header")


def test_row_on_first_of_month_counts_in_mtd_not_today():
    cf, _ = load_ledger(FIXTURES / "ledger_basic.csv", today=TODAY)
    # 2026-04-01 USD 500.00 is in MTD but not today
    assert Decimal("500.00") <= cf.mtd_usd
    assert cf.today_usd == Decimal("55.00")


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
    assert cf.today_usd == Decimal("20.00")
    assert exc == ["ledger row 2: too few columns"]
