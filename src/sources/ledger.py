from __future__ import annotations

import csv
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import Cashflow

SUPPORTED_CURRENCIES = ("USD", "CHF")
EXPECTED_HEADER = ["date", "amount", "currency", "category", "description"]
ZERO = Decimal("0.00")


def load_ledger(
    path: Path, today: Date
) -> tuple[Cashflow | None, list[str]]:
    """Read a ledger CSV and aggregate Today/MTD/Bal per supported currency.

    Returns (Cashflow, exceptions) on success (possibly with bad rows logged),
    or (None, [reason]) if the file or header is unusable.
    """
    if not path.exists():
        return None, ["ledger: file not found"]

    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                return None, ["ledger: empty file"]
            if [h.strip() for h in header] != EXPECTED_HEADER:
                return None, [f"ledger: unexpected header {header!r}"]
            rows = list(reader)
    except OSError as e:
        return None, [f"ledger: read error: {e}"]

    today_usd = today_chf = ZERO
    mtd_usd = mtd_chf = ZERO
    bal_usd = bal_chf = ZERO
    exceptions: list[str] = []
    unsupported: list[str] = []

    for n, raw in enumerate(rows, start=2):
        if not raw or all(c.strip() == "" for c in raw):
            continue
        if len(raw) < 3:
            exceptions.append(f"ledger row {n}: too few columns")
            continue
        date_s = raw[0].strip()
        amount_s = raw[1].strip()
        currency_s = raw[2].strip()

        try:
            d = Date.fromisoformat(date_s)
        except ValueError:
            exceptions.append(f"ledger row {n}: bad date '{date_s}'")
            continue

        try:
            amt = Decimal(amount_s)
        except (InvalidOperation, ValueError):
            exceptions.append(f"ledger row {n}: bad amount '{amount_s}'")
            continue

        ccy = currency_s.upper()
        if ccy not in SUPPORTED_CURRENCIES:
            unsupported.append(ccy)
            continue

        if d > today:
            continue

        if ccy == "USD":
            bal_usd += amt
            if d.year == today.year and d.month == today.month:
                mtd_usd += amt
            if d == today:
                today_usd += amt
        else:  # CHF
            bal_chf += amt
            if d.year == today.year and d.month == today.month:
                mtd_chf += amt
            if d == today:
                today_chf += amt

    if unsupported:
        codes = sorted(set(unsupported))
        exceptions.append(
            f"ledger: skipped {len(unsupported)} row(s) with unsupported currency: {','.join(codes)}"
        )

    cf = Cashflow(
        today_usd=today_usd,
        today_chf=today_chf,
        mtd_usd=mtd_usd,
        mtd_chf=mtd_chf,
        bal_usd=bal_usd,
        bal_chf=bal_chf,
    )
    return cf, exceptions
