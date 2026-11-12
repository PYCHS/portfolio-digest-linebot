import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.models import PricePoint
from src.sources.prices import load_prices, merge_live_prices

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


def test_invalid_utf8_returns_a_read_error(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_bytes(b"isin_or_code,price\nXS0000000001,99.25\xff\n")

    prices, exc = load_prices(path)

    assert prices is None
    assert len(exc) == 1
    assert exc[0].startswith("prices: read error:")


def test_oversized_csv_field_returns_a_read_error(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "isin_or_code,price,notes\n"
        "XS0000000001,99.25,valid row\n"
        "XS0000000002,100.00," + "x" * (csv.field_size_limit() + 1) + "\n",
        encoding="utf-8",
    )

    prices, exc = load_prices(path)

    assert prices is None
    assert len(exc) == 1
    assert exc[0].startswith("prices: read error: field larger than field limit")


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


def test_future_date_keeps_the_price_but_drops_the_date(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "isin_or_code,price,as_of\n"
        "XS0000000001,99.5000,2026-04-26\n",
        encoding="utf-8",
    )

    prices, exc = load_prices(path, today=date(2026, 4, 25))

    assert prices["XS0000000001"].price == Decimal("99.5000")
    assert prices["XS0000000001"].as_of is None
    assert exc == [
        "prices row 2: as_of 2026-04-26 is after digest date 2026-04-25"
    ]


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


def test_older_duplicate_does_not_replace_newer_dated_quote(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "isin_or_code,price,as_of\n"
        "XS0000000001,99.5000,2026-04-24\n"
        "XS0000000001,98.0000,2026-04-20\n",
        encoding="utf-8",
    )

    prices, exc = load_prices(path)

    assert prices["XS0000000001"].price == Decimal("99.5000")
    assert prices["XS0000000001"].as_of == date(2026, 4, 24)
    assert exc == [
        "prices: duplicate XS0000000001 (keeping newer 2026-04-24)"
    ]


def test_undated_duplicate_does_not_replace_dated_quote(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "isin_or_code,price,as_of\n"
        "XS0000000001,99.5000,2026-04-24\n"
        "XS0000000001,98.0000,\n",
        encoding="utf-8",
    )

    prices, exc = load_prices(path)

    assert prices["XS0000000001"].price == Decimal("99.5000")
    assert prices["XS0000000001"].as_of == date(2026, 4, 24)
    assert exc == [
        "prices: duplicate XS0000000001 "
        "(keeping dated 2026-04-24 over undated row)"
    ]


def test_compact_and_slash_date_formats_are_accepted():
    prices, exc = load_prices(FIXTURES / "prices_alt_date_formats.csv")
    assert exc == []
    assert prices["XS0000000001"].as_of == date(2026, 4, 24)
    assert prices["US0000000002"].as_of == date(2026, 4, 23)


def test_older_live_quote_does_not_replace_newer_stored_fallback():
    stored = {
        "XS0000000001": PricePoint(Decimal("99.50"), date(2026, 4, 24))
    }
    live = {
        "XS0000000001": PricePoint(Decimal("98.00"), date(2026, 4, 20))
    }

    merged, exc = merge_live_prices(stored, live)

    assert merged["XS0000000001"] == stored["XS0000000001"]
    assert exc == [
        "quotes XS0000000001: live quote dated 2026-04-20 is older than "
        "fallback 2026-04-24; keeping fallback"
    ]


def test_undated_live_quote_does_not_replace_dated_stored_fallback():
    stored = {
        "XS0000000001": PricePoint(Decimal("99.50"), date(2026, 4, 24))
    }
    live = {"XS0000000001": PricePoint(Decimal("98.00"), None)}

    merged, exc = merge_live_prices(stored, live)

    assert merged["XS0000000001"] == stored["XS0000000001"]
    assert exc == [
        "quotes XS0000000001: live quote has no date; keeping dated fallback "
        "2026-04-24"
    ]


def test_mixed_live_quotes_merge_independently_without_mutating_inputs():
    stored = {
        "STALE": PricePoint(Decimal("99.50"), date(2026, 4, 24)),
        "FRESH": PricePoint(Decimal("100.00"), date(2026, 4, 24)),
        "MISSING": PricePoint(Decimal("101.00"), date(2026, 4, 24)),
    }
    live = {
        # Rejecting this first entry must not abort the rest of the batch.
        "STALE": PricePoint(Decimal("98.00"), date(2026, 4, 20)),
        "FRESH": PricePoint(Decimal("102.00"), date(2026, 4, 25)),
        "NEW": PricePoint(Decimal("103.00"), date(2026, 4, 25)),
    }
    stored_before = dict(stored)
    live_before = dict(live)

    merged, exc = merge_live_prices(stored, live)

    assert merged == {
        "STALE": stored_before["STALE"],
        "FRESH": live_before["FRESH"],
        "MISSING": stored_before["MISSING"],
        "NEW": live_before["NEW"],
    }
    assert exc == [
        "quotes STALE: live quote dated 2026-04-20 is older than "
        "fallback 2026-04-24; keeping fallback"
    ]
    assert stored == stored_before
    assert live == live_before
