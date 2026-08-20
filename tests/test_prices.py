from datetime import date
from decimal import Decimal
from pathlib import Path

from src.sources.prices import load_prices

FIXTURES = Path(__file__).parent / "fixtures"


def test_basic_load_keys_by_isin_with_quote_dates():
    prices, exc = load_prices(FIXTURES / "prices_basic.csv")
    assert exc == []
    assert prices is not None
    assert set(prices) == {"XS0000000001", "US0000000002"}
    assert prices["XS0000000001"].price == Decimal("99.2500")
    assert prices["XS0000000001"].as_of == date(2026, 4, 24)
    assert prices["US0000000002"].as_of == date(2026, 4, 20)


def test_missing_file_is_a_silent_opt_out():
    """No quote file is a legitimate state (the feature is manual), so it must
    not manufacture an exception every single morning — the digest already
    reports the gap by rendering every holding as unquoted."""
    prices, exc = load_prices(FIXTURES / "does_not_exist.csv")
    assert prices is None
    assert exc == []


def test_missing_required_columns_is_reported():
    prices, exc = load_prices(FIXTURES / "prices_missing_columns.csv")
    assert prices is None
    assert exc == ["prices: missing columns ['price']"]


def test_bad_rows_are_skipped_individually_and_reported():
    prices, exc = load_prices(FIXTURES / "prices_bad_rows.csv")
    assert prices is not None
    # Only the good row survives as a usable quote.
    assert set(prices) == {"XS0000000001", "US0000000004"}
    assert prices["XS0000000001"].price == Decimal("99.2500")
    assert exc == [
        "prices row 3: missing isin_or_code",
        "prices row 4: bad price 'not-a-number'",
        "prices row 5: non-positive price '-4.00'",
        "prices row 6: bad as_of '24/04/2026'",
    ]


def test_unparseable_date_keeps_the_price_but_drops_the_date():
    """A quote with a mangled date is still a quote; losing the price over a
    typo in a column that only annotates it would be the worse trade."""
    prices, _ = load_prices(FIXTURES / "prices_bad_rows.csv")
    assert prices["US0000000004"].price == Decimal("101.00")
    assert prices["US0000000004"].as_of is None


def test_blank_price_rows_are_skipped_without_complaint():
    """prices.csv is meant to be seeded with every ISIN and filled in later,
    so a placeholder row is normal, not an error."""
    _, exc = load_prices(FIXTURES / "prices_bad_rows.csv")
    assert not any("row 7" in e for e in exc)


def test_duplicate_isin_takes_the_last_row_and_says_so():
    prices, exc = load_prices(FIXTURES / "prices_duplicate_isin.csv")
    # Case and stray whitespace must not create a second entry.
    assert set(prices) == {"XS0000000001"}
    assert prices["XS0000000001"].price == Decimal("99.5000")
    assert exc == ["prices: duplicate XS0000000001 (using last)"]


def test_compact_and_slash_date_formats_are_accepted():
    prices, exc = load_prices(FIXTURES / "prices_alt_date_formats.csv")
    assert exc == []
    assert prices["XS0000000001"].as_of == date(2026, 4, 24)
    assert prices["US0000000002"].as_of == date(2026, 4, 23)
