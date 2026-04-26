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


@dataclass(frozen=True)
class FxResult:
    usd_chf: Decimal
    chf_usd: Decimal
    usd_chf_dod_pct: Decimal | None


@dataclass(frozen=True)
class Cashflow:
    today_usd: Decimal
    today_chf: Decimal
    mtd_usd: Decimal
    mtd_chf: Decimal
    bal_usd: Decimal
    bal_chf: Decimal


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
