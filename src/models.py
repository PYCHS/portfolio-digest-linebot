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
    issuer_id: str
    date: date
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Snapshot:
    total_cost_usd: Decimal
    annual_coupon_usd: Decimal
    next_coupon: Coupon | None


@dataclass(frozen=True)
class DataGap:
    source: str
    reason: str


@dataclass(frozen=True)
class DigestInput:
    date_str: str
    news: list[NewsItem] | None
    fx: FxResult | None
    cashflow: Cashflow | None
    snapshot: Snapshot | None
    exceptions: list[str] = field(default_factory=list)
