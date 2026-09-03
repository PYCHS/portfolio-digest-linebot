from __future__ import annotations

import csv
from datetime import date as Date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from ..models import Coupon, HoldingPrice, PricePoint, Snapshot
from .coupon_dates import parse_coupon_month_days

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
HUNDRED = Decimal("100")


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
    for month, day in parse_coupon_month_days(coupon_dates):
        # Search through the next leap cycle so a legitimate 2/29 schedule
        # still finds its next occurrence from a non-leap year.
        for year in range(today.year, today.year + 5):
            try:
                candidate = Date(year, month, day)
            except ValueError:
                continue
            if candidate >= today:
                out.append(candidate)
                break
    return out


def _per_coupon_amount(
    annual: Decimal | None, semi: Decimal | None, n: int
) -> Decimal | None:
    if annual is None or n == 0:
        return None
    if n == 2 and semi is not None:
        return semi
    return (annual / Decimal(n)).quantize(TWO_DP)


def _mark_to_market(
    *,
    name: str,
    currency: str,
    buy_price: Decimal | None,
    quantity: Decimal | None,
    quote: PricePoint | None,
) -> HoldingPrice:
    """Compare one holding's quote against its entry price.

    Both prices are clean quotes per 100 face, so the percentage move is
    directly comparable across holdings and P/L is quantity x points / 100.
    Every derived field degrades to None independently: an unquoted bond
    still gets a row so the digest can say the quote is missing.
    """
    change_pct: Decimal | None = None
    pnl: Decimal | None = None
    if quote is not None and buy_price is not None and buy_price > 0:
        change_pct = (
            (quote.price - buy_price) / buy_price * HUNDRED
        ).quantize(TWO_DP, rounding=ROUND_HALF_UP)
        if quantity is not None:
            pnl = (
                (quote.price - buy_price) / HUNDRED * quantity
            ).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    return HoldingPrice(
        name=name,
        currency=currency,
        buy_price=buy_price,
        current_price=quote.price if quote else None,
        as_of=quote.as_of if quote else None,
        change_pct=change_pct,
        pnl=pnl,
    )


def load_positions(
    path: Path,
    today: Date,
    *,
    default_currency: str = "USD",
    prices: dict[str, PricePoint] | None = None,
) -> tuple[Snapshot | None, list[str]]:
    """Read the positions CSV and produce a Snapshot.

    Rows where `cost` or `buy_price` is missing are excluded from
    `total_cost` and recorded in `uncosted_issuers` instead. Their
    `annual_interest` is still summed into `annual_coupon` if present.
    Currency is read from a `currency` column when present, else
    `default_currency` (USD).

    `prices` (M12) is an ISIN-keyed quote map from prices.csv. A row joins it
    on `isin_or_code`; bonds get a price row even without a quote, so a gap
    in the file is visible in the digest, while unquoted structured notes —
    which have no market price to begin with — stay out of the section.
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
    holding_prices: list[HoldingPrice] = []
    unrealized: dict[str, Decimal] = {}
    quote_dates: list[Date] = []

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

        isin = (row.get("isin_or_code") or "").strip().upper()
        quote = prices.get(isin) if (prices and isin) else None
        instrument = (row.get("instrument_type") or "").strip().lower()
        if quote is not None or instrument == "bond":
            qty = (
                _parse_decimal_field(row, "quantity", n, exceptions)
                if quote is not None
                else None
            )
            mark = _mark_to_market(
                name=issuer,
                currency=ccy,
                buy_price=buy_price,
                quantity=qty,
                quote=quote,
            )
            holding_prices.append(mark)
            if mark.pnl is not None:
                unrealized[ccy] = unrealized.get(ccy, Decimal("0.00")) + mark.pnl
            if mark.as_of is not None:
                quote_dates.append(mark.as_of)

        annual = _parse_decimal_field(row, "annual_interest", n, exceptions)
        semi = _parse_decimal_field(row, "semiannual_interest", n, exceptions)
        if annual is not None:
            annual_coupon[ccy] = annual_coupon.get(ccy, Decimal("0.00")) + annual

        coupon_dates_raw = (row.get("coupon_dates") or "").strip()
        if coupon_dates_raw:
            try:
                occurrences = _next_occurrences(coupon_dates_raw, today)
            except ValueError as exc:
                exceptions.append(f"positions row {n}: {exc}")
                continue
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
        prices=holding_prices,
        unrealized=unrealized,
        price_as_of_min=min(quote_dates) if quote_dates else None,
        price_as_of_max=max(quote_dates) if quote_dates else None,
    )
    return snapshot, exceptions
