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

    A missing file is a supported opt-out — the ledger tracks *actuals*, and
    the user may legitimately not be tracking any yet. In that case we return
    a zero-Cashflow with no exception so the digest renders cleanly without a
    Notes/Data-gap entry.

    A broken file (empty / bad header / unreadable) still returns
    (None, [reason]) so the digest surfaces it.
    """
    today_flow: dict[str, Decimal] = {ccy: ZERO for ccy in SUPPORTED_CURRENCIES}
    mtd_flow: dict[str, Decimal] = {ccy: ZERO for ccy in SUPPORTED_CURRENCIES}
    bal_flow: dict[str, Decimal] = {ccy: ZERO for ccy in SUPPORTED_CURRENCIES}

    if not path.exists():
        return Cashflow(today=today_flow, mtd=mtd_flow, bal=bal_flow), []

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
        if not amt.is_finite():
            exceptions.append(f"ledger row {n}: non-finite amount '{amount_s}'")
            continue

        ccy = currency_s.upper()
        if ccy not in SUPPORTED_CURRENCIES:
            unsupported.append(ccy)
            continue

        if d > today:
            continue

        bal_flow[ccy] += amt
        if d.year == today.year and d.month == today.month:
            mtd_flow[ccy] += amt
        if d == today:
            today_flow[ccy] += amt

    if unsupported:
        codes = sorted(set(unsupported))
        exceptions.append(
            f"ledger: skipped {len(unsupported)} row(s) with unsupported currency: {','.join(codes)}"
        )

    return Cashflow(today=today_flow, mtd=mtd_flow, bal=bal_flow), exceptions
