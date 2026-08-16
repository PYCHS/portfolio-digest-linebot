"""M9 — Projected cashflow calendar.

Two inputs, both optional (missing file = clean opt-out, like the ledger):

1. `positions.csv` — bond coupon schedules already live here
   (`coupon_dates` + `semiannual_interest` / `annual_interest`). Each
   occurrence inside the horizon becomes an inflow event.

2. `recurring.csv` — everything the positions schema can't express:
   FCN monthly coupons with an end date, loan / reverse-repo interest
   outflows, insurance payouts, one-off redemptions.

   Header: label,currency,amount,schedule,start_date,end_date,category,estimate
   - schedule:  monthly:<D>            e.g. monthly:8   (day-of-month)
                yearly:<M/D[;M/D...]>  e.g. yearly:3/4;6/4;9/4;12/4
                once:<YYYY-MM-DD>
   - start_date / end_date: optional ISO bounds (inclusive)
   - category: coupon | principal | interest | insurance | other
   - estimate: 1 marks the amount/date as an estimate (rendered "(預估)")
"""
from __future__ import annotations

import csv
from datetime import date as Date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import CashflowEvent, Projection

RECURRING_HEADER = [
    "label",
    "currency",
    "amount",
    "schedule",
    "start_date",
    "end_date",
    "category",
    "estimate",
]
CATEGORIES = {"coupon", "principal", "interest", "insurance", "other"}
TWO_DP = Decimal("0.01")

# How far past the horizon to keep expanding when hunting for the next inflow.
# Just over a year, so an annual payer (insurance dividend, yearly coupon) is
# always caught even when today sits right after its payment date.
INFLOW_LOOKAHEAD_DAYS = 400


def _safe_date(year: int, month: int, day: int) -> Date | None:
    try:
        return Date(year, month, day)
    except ValueError:
        return None


def _parse_iso(raw: str) -> Date | None:
    raw = raw.strip()
    if not raw:
        return None
    return Date.fromisoformat(raw)  # raises ValueError for the caller


def _monthly_occurrences(day: int, lo: Date, hi: Date) -> list[Date]:
    out: list[Date] = []
    y, m = lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        d = _safe_date(y, m, day)
        if d and lo <= d <= hi:
            out.append(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _yearly_occurrences(md_list: str, lo: Date, hi: Date) -> list[Date]:
    out: list[Date] = []
    for raw in md_list.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        m_s, d_s = raw.split("/")
        m, d = int(m_s), int(d_s)
        for year in range(lo.year, hi.year + 1):
            cand = _safe_date(year, m, d)
            if cand and lo <= cand <= hi:
                out.append(cand)
    return out


def _expand_schedule(
    schedule: str, lo: Date, hi: Date
) -> list[Date]:
    """Expand a schedule expression into dates within [lo, hi].

    Raises ValueError on malformed expressions so the caller can attribute
    the error to the right CSV row.
    """
    kind, _, arg = schedule.partition(":")
    kind = kind.strip().lower()
    arg = arg.strip()
    if kind == "monthly":
        day = int(arg)
        if not 1 <= day <= 31:
            raise ValueError(f"bad day-of-month {arg!r}")
        return _monthly_occurrences(day, lo, hi)
    if kind == "yearly":
        return _yearly_occurrences(arg, lo, hi)
    if kind == "once":
        d = Date.fromisoformat(arg)
        return [d] if lo <= d <= hi else []
    raise ValueError(f"unknown schedule kind {kind!r}")


def _load_recurring_events(
    path: Path, today: Date, horizon_end: Date, exceptions: list[str]
) -> list[CashflowEvent]:
    if not path.exists():
        return []

    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                exceptions.append("recurring: empty file")
                return []
            if [h.strip() for h in header] != RECURRING_HEADER:
                exceptions.append(f"recurring: unexpected header {header!r}")
                return []
            rows = list(reader)
    except OSError as e:
        exceptions.append(f"recurring: read error: {e}")
        return []

    events: list[CashflowEvent] = []
    for n, raw in enumerate(rows, start=2):
        if not raw or all(c.strip() == "" for c in raw):
            continue
        if len(raw) < len(RECURRING_HEADER):
            exceptions.append(f"recurring row {n}: too few columns")
            continue
        label = raw[0].strip()
        ccy = raw[1].strip().upper()
        if not label or not ccy:
            exceptions.append(f"recurring row {n}: missing label/currency")
            continue
        try:
            amount = Decimal(raw[2].strip())
            if not amount.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            exceptions.append(f"recurring row {n}: bad amount {raw[2]!r}")
            continue
        try:
            start = _parse_iso(raw[4]) or today
            end = _parse_iso(raw[5]) or horizon_end
        except ValueError:
            exceptions.append(f"recurring row {n}: bad start/end date")
            continue
        lo = max(today, start)
        hi = min(horizon_end, end)
        if lo > hi:
            continue
        try:
            dates = _expand_schedule(raw[3].strip(), lo, hi)
        except ValueError as e:
            exceptions.append(f"recurring row {n}: {e}")
            continue
        category = raw[6].strip().lower() or "other"
        if category not in CATEGORIES:
            exceptions.append(f"recurring row {n}: unknown category {raw[6]!r}")
            category = "other"
        is_estimate = raw[7].strip().lower() in {"1", "true", "yes"}
        for d in dates:
            events.append(
                CashflowEvent(
                    date=d,
                    label=label,
                    amount=amount,
                    currency=ccy,
                    category=category,
                    is_estimate=is_estimate,
                )
            )
    return events


def _load_position_coupon_events(
    path: Path, today: Date, horizon_end: Date, exceptions: list[str]
) -> list[CashflowEvent]:
    """Expand `coupon_dates` from positions.csv into coupon inflows.

    Deliberately forgiving: rows without coupon data are simply skipped —
    positions.py already reports structural problems with this file.
    """
    if not path.exists():
        return []

    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except OSError as e:
        exceptions.append(f"schedule: positions read error: {e}")
        return []

    events: list[CashflowEvent] = []
    for row in rows:
        issuer = (row.get("issuer_or_name") or "").strip()
        coupon_dates = (row.get("coupon_dates") or "").strip()
        if not issuer or not coupon_dates:
            continue
        ccy = (row.get("currency") or "").strip().upper() or "USD"

        def _dec(key: str) -> Decimal | None:
            raw = (row.get(key) or "").strip()
            if not raw:
                return None
            try:
                val = Decimal(raw)
                return val if val.is_finite() else None
            except (InvalidOperation, ValueError):
                return None

        annual = _dec("annual_interest")
        semi = _dec("semiannual_interest")

        mds: list[tuple[int, int]] = []
        for part in coupon_dates.split(";"):
            part = part.strip()
            if not part:
                continue
            try:
                m_s, d_s = part.split("/")
                mds.append((int(m_s), int(d_s)))
            except ValueError:
                continue
        if not mds:
            continue

        n_per_year = len(mds)
        if n_per_year == 2 and semi is not None:
            amount = semi
        elif annual is not None:
            amount = (annual / Decimal(n_per_year)).quantize(TWO_DP)
        else:
            continue

        for m, d in mds:
            for year in range(today.year, horizon_end.year + 1):
                cand = _safe_date(year, m, d)
                if cand and today <= cand <= horizon_end:
                    events.append(
                        CashflowEvent(
                            date=cand,
                            label=f"{issuer} 配息",
                            amount=amount,
                            currency=ccy,
                            category="coupon",
                        )
                    )
    return events


def project_cashflows(
    positions_path: Path,
    recurring_path: Path,
    today: Date,
    *,
    horizon_days: int = 60,
) -> tuple[Projection | None, list[str]]:
    """Build the projected-cashflow calendar for [today, today+horizon].

    Events are expanded over a longer lookahead than the horizon so the next
    inflow can be reported even when it falls outside it — with semiannual
    coupons a 60-day horizon frequently contains no money-in at all.
    """
    horizon_end = today + timedelta(days=horizon_days)
    lookahead_end = max(horizon_end, today + timedelta(days=INFLOW_LOOKAHEAD_DAYS))
    exceptions: list[str] = []

    all_events = _load_position_coupon_events(
        positions_path, today, lookahead_end, exceptions
    )
    all_events.extend(
        _load_recurring_events(recurring_path, today, lookahead_end, exceptions)
    )
    all_events.sort(key=lambda e: (e.date, -e.amount))

    events = [e for e in all_events if e.date <= horizon_end]
    net: dict[str, Decimal] = {}
    for e in events:
        net[e.currency] = net.get(e.currency, Decimal("0.00")) + e.amount

    next_inflow = next((e for e in all_events if e.amount > 0), None)

    return (
        Projection(
            events=events,
            horizon_days=horizon_days,
            net=net,
            next_inflow=next_inflow,
            next_inflow_days=(
                (next_inflow.date - today).days if next_inflow else None
            ),
        ),
        exceptions,
    )
