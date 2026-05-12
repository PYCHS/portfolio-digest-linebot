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
class Snapshot:
    total_cost: dict[str, Decimal]
    annual_coupon: dict[str, Decimal]
    next_coupon: Coupon | None
    uncosted_issuers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DigestInput:
    date_str: str
    news: list[NewsItem] | None
    fx: FxResult | None
    cashflow: Cashflow | None
    snapshot: Snapshot | None
    exceptions: list[str] = field(default_factory=list)
