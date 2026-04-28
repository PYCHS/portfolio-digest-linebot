from __future__ import annotations

import csv
from datetime import date as Date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import Coupon, Snapshot

REQUIRED_COLUMNS = {
    "instrument_type",
    "issuer_or_name",
    "buy_price",
    "cost",
    "annual_interest",
    "semiannual_interest",
    "coupon_dates",
}
COUPON_LOOKAHEAD_DAYS = 7
TWO_DP = Decimal("0.01")


def _parse_decimal_field(
    row: dict[str, str | None], key: str, row_n: int, exceptions: list[str]
) -> Decimal | None:
    raw = row.get(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        val = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        exceptions.append(f"positions row {row_n}: bad {key} '{raw}'")
        return None
    if not val.is_finite():
        exceptions.append(f"positions row {row_n}: non-finite {key} '{raw}'")
        return None
    return val


def _next_occurrences(coupon_dates: str, today: Date) -> list[Date]:
    out: list[Date] = []
    for raw in coupon_dates.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            m_s, d_s = raw.split("/")
            m, d = int(m_s), int(d_s)
            candidate = Date(today.year, m, d)
            if candidate < today:
                candidate = Date(today.year + 1, m, d)
        except (ValueError, KeyError):
            continue
        out.append(candidate)
    return out


def _per_coupon_amount(
    annual: Decimal | None, semi: Decimal | None, n: int
) -> Decimal | None:
    if annual is None or n == 0:
        return None
    if n == 2 and semi is not None:
        return semi
    return (annual / Decimal(n)).quantize(TWO_DP)


def load_positions(
    path: Path,
    today: Date,
    *,
    default_currency: str = "USD",
) -> tuple[Snapshot | None, list[str]]:
    """Read the positions CSV and produce a Snapshot.

    Rows where `cost` or `buy_price` is missing are excluded from
    `total_cost` and recorded in `uncosted_issuers` instead. Their
    `annual_interest` is still summed into `annual_coupon` if present.
    Currency is read from a `currency` column when present, else
    `default_currency` (USD).
    """
    if not path.exists():
        return None, ["positions: file not found"]

    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            missing = REQUIRED_COLUMNS - set(fieldnames)
            if missing:
                return None, [f"positions: missing columns {sorted(missing)}"]
            rows = list(reader)
    except OSError as e:
        return None, [f"positions: read error: {e}"]

    has_currency_col = "currency" in fieldnames
    default_ccy = default_currency.upper()
    window_end = today + timedelta(days=COUPON_LOOKAHEAD_DAYS)

    total_cost: dict[str, Decimal] = {}
    annual_coupon: dict[str, Decimal] = {}
    uncosted_issuers: list[str] = []
    candidates: list[tuple[int, Coupon]] = []
    exceptions: list[str] = []

    for n, row in enumerate(rows, start=2):
        issuer = (row.get("issuer_or_name") or "").strip()
        if not issuer:
            exceptions.append(f"positions row {n}: missing issuer_or_name")
            continue

        ccy_raw = row.get("currency", "") if has_currency_col else ""
        ccy = (ccy_raw or "").strip().upper() or default_ccy

        cost = _parse_decimal_field(row, "cost", n, exceptions)
        buy_price = _parse_decimal_field(row, "buy_price", n, exceptions)
        if cost is None or buy_price is None:
            uncosted_issuers.append(issuer)
        else:
            total_cost[ccy] = total_cost.get(ccy, Decimal("0.00")) + cost

        annual = _parse_decimal_field(row, "annual_interest", n, exceptions)
        semi = _parse_decimal_field(row, "semiannual_interest", n, exceptions)
        if annual is not None:
            annual_coupon[ccy] = annual_coupon.get(ccy, Decimal("0.00")) + annual

        coupon_dates_raw = (row.get("coupon_dates") or "").strip()
        if coupon_dates_raw:
            occurrences = _next_occurrences(coupon_dates_raw, today)
            in_window = [d for d in occurrences if today <= d <= window_end]
            if in_window:
                amt = _per_coupon_amount(annual, semi, len(occurrences))
                if amt is not None:
                    next_date = min(in_window)
                    candidates.append(
                        (n, Coupon(issuer=issuer, date=next_date, amount=amt, currency=ccy))
                    )

    candidates.sort(key=lambda c: (c[1].date, c[0]))
    next_coupon = candidates[0][1] if candidates else None

    snapshot = Snapshot(
        total_cost=total_cost,
        annual_coupon=annual_coupon,
        next_coupon=next_coupon,
        uncosted_issuers=uncosted_issuers,
    )
    return snapshot, exceptions
