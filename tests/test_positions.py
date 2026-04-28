from datetime import date
from decimal import Decimal
from pathlib import Path

from src.sources.positions import load_positions

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 4, 25)


def test_basic_load_sums_per_currency_and_picks_next_coupon():
    snap, exc = load_positions(FIXTURES / "positions_basic.csv", today=TODAY)
    assert exc == []
    assert snap is not None
    assert snap.total_cost == {"USD": Decimal("1680000.00")}
    assert snap.annual_coupon == {"USD": Decimal("84500.00")}
    assert snap.uncosted_issuers == []
    assert snap.next_coupon is not None
    # Beta has 4/26 (one day after today) — earliest in 7d window
    assert snap.next_coupon.issuer == "Issuer Beta"
    assert snap.next_coupon.date == date(2026, 4, 26)
    assert snap.next_coupon.amount == Decimal("11250.00")
    assert snap.next_coupon.currency == "USD"


def test_uncosted_row_excluded_from_total_cost_but_annual_coupon_still_summed():
    snap, exc = load_positions(FIXTURES / "positions_with_uncosted.csv", today=TODAY)
    assert exc == []
    assert snap is not None
    # FCN row contributes 1200.00 to annual_coupon but nothing to total_cost
    assert snap.total_cost == {"USD": Decimal("985000.00")}
    assert snap.annual_coupon == {"USD": Decimal("51200.00")}
    assert snap.uncosted_issuers == ["Sample FCN"]


def test_currency_column_used_when_present():
    snap, exc = load_positions(
        FIXTURES / "positions_with_currency_column.csv", today=TODAY
    )
    assert exc == []
    assert snap is not None
    assert snap.total_cost == {
        "USD": Decimal("985000.00"),
        "CHF": Decimal("495000.00"),
    }
    assert snap.annual_coupon == {
        "USD": Decimal("50000.00"),
        "CHF": Decimal("22500.00"),
    }


def test_default_currency_when_no_currency_column():
    snap, _ = load_positions(FIXTURES / "positions_basic.csv", today=TODAY)
    assert set(snap.total_cost.keys()) == {"USD"}


def test_default_currency_override_takes_effect():
    snap, _ = load_positions(
        FIXTURES / "positions_basic.csv", today=TODAY, default_currency="CHF"
    )
    assert set(snap.total_cost.keys()) == {"CHF"}


def test_no_coupon_in_window_returns_none():
    snap, _ = load_positions(
        FIXTURES / "positions_with_currency_column.csv", today=TODAY
    )
    # USD: 5/01 in window; CHF: 6/01 + 12/01 both out — picks USD 5/01
    assert snap.next_coupon is not None
    assert snap.next_coupon.date == date(2026, 5, 1)


def test_year_rollover_for_january_coupon(tmp_path):
    csv_path = tmp_path / "positions_year_rollover.csv"
    csv_path.write_text(
        "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
        "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
        "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
        "bond,Year-End Issuer,XS0000000099,20250101,1000,5.00,2030,"
        "100.00,100000.00,5000.00,2500.00,5.00,5.0000,1/05;7/05\n",
        encoding="utf-8",
    )
    snap, _ = load_positions(csv_path, today=date(2026, 12, 30))
    assert snap.next_coupon is not None
    assert snap.next_coupon.date == date(2027, 1, 5)


def test_missing_required_columns_returns_full_gap():
    snap, exc = load_positions(FIXTURES / "positions_missing_columns.csv", today=TODAY)
    assert snap is None
    assert exc and exc[0].startswith("positions: missing columns")


def test_missing_file_returns_full_gap():
    snap, exc = load_positions(FIXTURES / "does_not_exist.csv", today=TODAY)
    assert snap is None
    assert exc == ["positions: file not found"]


def test_bad_rows_logged_and_buy_price_failure_treats_row_as_uncosted():
    snap, exc = load_positions(FIXTURES / "positions_bad_data.csv", today=TODAY)
    assert snap is not None
    # Row 2 (Alpha) skipped entirely (missing issuer), Row 3 (Beta) treated
    # as uncosted because buy_price could not be parsed, Row 4 (Gamma) costed.
    assert snap.total_cost == {"USD": Decimal("200000.00")}
    assert snap.annual_coupon == {"USD": Decimal("34500.00")}
    assert snap.uncosted_issuers == ["Issuer Beta"]
    joined = " | ".join(exc)
    assert "missing issuer_or_name" in joined
    assert "bad buy_price" in joined


def test_per_coupon_amount_uses_semiannual_field_when_two_dates():
    snap, _ = load_positions(FIXTURES / "positions_basic.csv", today=TODAY)
    # Beta semi=11250.00 (from CSV), not annual/2=11250 (would coincide,
    # but the semi field is preferred — verify the value is exactly that).
    assert snap.next_coupon.amount == Decimal("11250.00")


def test_per_coupon_amount_divides_when_more_than_two_dates(tmp_path):
    # An instrument with 4 coupon dates (quarterly), no semiannual field
    csv_path = tmp_path / "quarterly.csv"
    csv_path.write_text(
        "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
        "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
        "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
        "note,Quarterly Issuer,,20260101,1000,4.00,2030,"
        "100.00,100000.00,4000.00,,4.00,4.0000,4/30;7/30;10/30;1/30\n",
        encoding="utf-8",
    )
    snap, _ = load_positions(csv_path, today=TODAY)
    # Earliest is 4/30 (5 days). Per-coupon = 4000/4 = 1000.00
    assert snap.next_coupon is not None
    assert snap.next_coupon.date == date(2026, 4, 30)
    assert snap.next_coupon.amount == Decimal("1000.00")


def test_non_finite_buy_price_rejected_and_row_treated_as_uncosted(tmp_path):
    csv_path = tmp_path / "nonfinite.csv"
    csv_path.write_text(
        "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
        "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
        "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
        # Infinity in buy_price → the row is "uncosted" because parse rejects it
        "bond,Bad Issuer,XS0,20250101,100,5.00,2030,Infinity,10000.00,500.00,250.00,5,5,5/01\n",
        encoding="utf-8",
    )
    snap, exc = load_positions(csv_path, today=TODAY)
    assert snap is not None
    assert snap.uncosted_issuers == ["Bad Issuer"]
    assert any("non-finite buy_price" in e for e in exc)


def test_malformed_semiannual_interest_logged_even_when_coupon_out_of_window(tmp_path):
    # Regression for B1: semiannual_interest parsing previously only happened
    # inside the in-window branch, so a row with a malformed semi field plus a
    # far-future coupon date silently swallowed the error.
    csv_path = tmp_path / "bad_semi_far_coupon.csv"
    csv_path.write_text(
        "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
        "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
        "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
        "bond,Far Issuer,XS0,20250101,100,5.00,2030,"
        "100.00,10000.00,500.00,not-a-num,5,5,12/01\n",
        encoding="utf-8",
    )
    snap, exc = load_positions(csv_path, today=TODAY)
    assert snap is not None
    # Coupon is in December — out of 7d window — so next_coupon is None.
    assert snap.next_coupon is None
    # But the malformed semiannual_interest must still be flagged.
    assert any("bad semiannual_interest" in e for e in exc), exc
