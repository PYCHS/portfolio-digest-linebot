from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NewsItem:
    issuer_id: str
    summary: str
    source: str
    is_alert: bool = False
    link: str | None = None
    # LLM enrichment (M10). None when analysis is unavailable — the
    # formatter then falls back to the plain headline rendering.
    summary_zh: str | None = None
    impact: str | None = None  # 利多 / 利空 / 中性 / 無影響
    impact_reason: str | None = None


@dataclass(frozen=True)
class FxResult:
    usd_chf: Decimal
    chf_usd: Decimal
    usd_chf_dod_pct: Decimal | None
    # Calendar date the latest rate is observed for. Frankfurter publishes
    # weekday rates around 15:00 CET, so a digest fired earlier than that
    # (or on a weekend / holiday) gets a rate dated to a prior business day.
    # The formatter surfaces this when it doesn't match the digest's date.
    as_of: date | None = None


@dataclass(frozen=True)
class Cashflow:
    today: dict[str, Decimal]
    mtd: dict[str, Decimal]
    bal: dict[str, Decimal]


@dataclass(frozen=True)
class Coupon:
    issuer: str
    date: date
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class CashflowEvent:
    """One projected cash movement (M9).

    `category` drives rendering: "interest" rows are flagged as loan-interest
    outflows (💸), "coupon"/"principal"/"insurance"/"other" render plain.
    `is_estimate` marks rows whose amount or date is an estimate rather than
    a contractual figure.
    """

    date: date
    label: str
    amount: Decimal
    currency: str
    category: str = "other"
    is_estimate: bool = False


@dataclass(frozen=True)
class Projection:
    """Cashflow calendar for the horizon, plus the next inflow beyond it.

    `events` / `net` cover [today, today+horizon_days]. `next_inflow` is the
    first money-in event found over a much longer lookahead, because with
    semiannual bond coupons the horizon is regularly all-outflow and "when
    does money next arrive?" is the question that actually gets asked.
    """

    events: list[CashflowEvent]
    horizon_days: int
    net: dict[str, Decimal]
    next_inflow: CashflowEvent | None = None
    next_inflow_days: int | None = None


@dataclass(frozen=True)
class PricePoint:
    """One manually-maintained market quote (M12).

    Corporate bonds have no dependable free quote API, so `price` comes from
    the broker statement via prices.csv rather than a live feed. `as_of` is
    what makes that honest: the digest shows the quote date so a file nobody
    has updated in a month reads as stale instead of as today's market.
    """

    price: Decimal
    as_of: date | None = None


@dataclass(frozen=True)
class HoldingPrice:
    """A holding marked against its entry price (M12).

    Prices are clean quotes per 100 face, matching `buy_price` in
    positions.csv, so `change_pct` and `pnl` are comparable across holdings
    of very different face values. Any field may be None — an unquoted bond
    still renders, it just says so.
    """

    name: str
    currency: str
    buy_price: Decimal | None = None
    current_price: Decimal | None = None
    as_of: date | None = None
    change_pct: Decimal | None = None
    pnl: Decimal | None = None


@dataclass(frozen=True)
class Snapshot:
    total_cost: dict[str, Decimal]
    annual_coupon: dict[str, Decimal]
    next_coupon: Coupon | None
    uncosted_issuers: list[str] = field(default_factory=list)
    # M12 — current vs. entry price, in positions.csv order.
    prices: list[HoldingPrice] = field(default_factory=list)
    # Unrealized P/L per currency, summed over quoted holdings only.
    unrealized: dict[str, Decimal] = field(default_factory=dict)
    # Oldest / newest quote date backing `prices`, for the staleness note.
    price_as_of_min: date | None = None
    price_as_of_max: date | None = None


@dataclass(frozen=True)
class DigestInput:
    date_str: str
    news: list[NewsItem] | None
    fx: FxResult | None
    cashflow: Cashflow | None
    snapshot: Snapshot | None
    exceptions: list[str] = field(default_factory=list)
    projected: Projection | None = None
    # Consolidated Traditional-Chinese news paragraph from the LLM analysis.
    # When present it *replaces* the per-item list in the rendered digest.
    # None when the LLM step didn't run — the formatter then falls back to
    # bare headlines.
    news_overview: str | None = None
    # M11 — morning greeting block (早安 + 勉勵 + 笑話), rendered right
    # under the title. None hides the block entirely.
    greeting: str | None = None
