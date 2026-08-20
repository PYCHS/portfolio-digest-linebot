"""Manually-maintained market quotes for the holdings (M12).

There is no dependable free quote feed for the corporate bonds in this book,
so prices come from the broker statement through `private/prices.csv` instead
of an API. The file is deliberately its own CSV rather than extra columns on
positions.csv: prices change on their own cadence, and in the GitHub Actions
deployment that means refreshing one small secret instead of re-uploading the
whole positions file.

Quotes are clean prices per 100 face, the same convention as `buy_price` in
positions.csv, so the two are directly comparable.
"""
from __future__ import annotations

import csv
from datetime import date as Date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import PricePoint

REQUIRED_COLUMNS = {"isin_or_code", "price"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")


def _parse_as_of(raw: str, row_n: int, exceptions: list[str]) -> Date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    exceptions.append(f"prices row {row_n}: bad as_of '{raw}'")
    return None


def load_prices(path: Path) -> tuple[dict[str, PricePoint] | None, list[str]]:
    """Read prices.csv into an ISIN-keyed quote map.

    Returns (None, [reason]) when the file is absent or unusable, which the
    formatter renders as a data gap rather than as "no change". Rows with a
    blank price are skipped silently on purpose: seeding the file with every
    ISIN and filling the numbers in later is the expected workflow.
    """
    if not path.exists():
        return None, []

    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            missing = REQUIRED_COLUMNS - set(fieldnames)
            if missing:
                return None, [f"prices: missing columns {sorted(missing)}"]
            rows = list(reader)
    except OSError as e:
        return None, [f"prices: read error: {e}"]

    has_as_of = "as_of" in fieldnames
    out: dict[str, PricePoint] = {}
    exceptions: list[str] = []

    for n, row in enumerate(rows, start=2):
        isin = (row.get("isin_or_code") or "").strip().upper()
        if not isin:
            exceptions.append(f"prices row {n}: missing isin_or_code")
            continue

        raw_price = (row.get("price") or "").strip()
        if not raw_price:
            continue
        try:
            price = Decimal(raw_price)
        except (InvalidOperation, ValueError):
            exceptions.append(f"prices row {n}: bad price '{raw_price}'")
            continue
        if not price.is_finite() or price <= 0:
            exceptions.append(f"prices row {n}: non-positive price '{raw_price}'")
            continue

        as_of: Date | None = None
        raw_as_of = (row.get("as_of") or "").strip() if has_as_of else ""
        if raw_as_of:
            as_of = _parse_as_of(raw_as_of, n, exceptions)

        # Last row wins, so appending a fresh quote to the bottom of the file
        # does what it looks like it does. The duplicate is still reported.
        if isin in out:
            exceptions.append(f"prices: duplicate {isin} (using last)")
        out[isin] = PricePoint(price=price, as_of=as_of)

    return out, exceptions
