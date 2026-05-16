from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import DigestInput

YIELD_QUANTUM = Decimal("0.01")


def render(d: DigestInput) -> str:
    lines: list[str] = []
    lines.append(
        f"【Daily Investment Digest (每日投資摘要)】{d.date_str} (Asia/Taipei)"
    )
    lines.append("")

    is_alert = bool(d.news) and any(n.is_alert for n in d.news)
    lines.append(
        "⚠️ Status (狀態): Alert (警報)"
        if is_alert
        else "✅ Status (狀態): All clear (一切正常)"
    )
    lines.append("")

    lines.append("\U0001f4f0 News (新聞) (0–1 per issuer)")
    if d.news is None:
        lines.append("- (unavailable / 無資料)")
    elif not d.news:
        lines.append("- (no items / 無)")
    else:
        for n in d.news:
            lines.append(f"- {n.issuer_id}: {n.summary} ({n.source})")
            if n.link:
                lines.append(f"  {n.link}")
    lines.append("")

    lines.append("\U0001f4b1 FX (匯率)")
    if d.fx is None:
        lines.append("- (unavailable / 無資料)")
    else:
        if d.fx.usd_chf_dod_pct is None:
            dod = "Δ N/A"
        else:
            dod = f"Δ {d.fx.usd_chf_dod_pct:+.2f}% DoD"
        # Surface staleness when the rate's as-of date isn't today (Taipei).
        # Frankfurter publishes weekday rates around 15:00 CET, so a digest
        # fired before then — or on a weekend / holiday — gets an earlier
        # business day's rate; saying so up front avoids "is this today's
        # number?" confusion.
        if d.fx.as_of and d.fx.as_of.isoformat() != d.date_str:
            stale = f", as of {d.fx.as_of.isoformat()}"
        else:
            stale = ""
        lines.append(f"- USD/CHF: {d.fx.usd_chf:.4f} ({dod}{stale})")
        lines.append(f"- CHF/USD: {d.fx.chf_usd:.4f}")
    lines.append("")

    lines.append("\U0001f4cc Portfolio Snapshot (投資組合快照) (Cost-based)")
    if d.snapshot is None:
        lines.append("- (unavailable / 無資料)")
    else:
        s = d.snapshot
        for ccy in sorted(s.total_cost):
            lines.append(f"- Total Cost (總成本): {s.total_cost[ccy]:,.2f} {ccy}")
        for ccy in sorted(s.annual_coupon):
            coupon = s.annual_coupon[ccy]
            cost = s.total_cost.get(ccy)
            # Yield is the conventional bond-portfolio metric (coupon as a
            # percentage of cost basis); show it inline when cost data is
            # present so the reader doesn't have to do the division. Skip when
            # cost is missing or zero — printing "0.00% yield" would be
            # misleading vs. saying nothing.
            if cost and cost > 0:
                yield_pct = (coupon / cost * Decimal(100)).quantize(
                    YIELD_QUANTUM, rounding=ROUND_HALF_UP
                )
                suffix = f" ({yield_pct:.2f}% yield / 殖利率)"
            else:
                suffix = ""
            lines.append(
                f"- Est. Annual Coupon (預估年息): {coupon:,.2f} {ccy}{suffix}"
            )
        if s.next_coupon is None:
            lines.append("- Next Coupon (7d) (七日內下個利息): none (無)")
        else:
            c = s.next_coupon
            lines.append(
                f"- Next Coupon (7d) (七日內下個利息): {c.issuer} {c.date.isoformat()} "
                f"{c.amount:,.2f} {c.currency}"
            )
        for issuer in s.uncosted_issuers:
            lines.append(f"- {issuer}: Cost unavailable (成本不明)")
    lines.append("")

    lines.append("\U0001f4b0 Cashflow (現金流) (Ledger)")
    if d.cashflow is None:
        lines.append("- (unavailable / 無資料)")
    else:
        cf = d.cashflow
        zero = Decimal("0.00")
        today_usd = cf.today.get("USD", zero)
        today_chf = cf.today.get("CHF", zero)
        mtd_usd = cf.mtd.get("USD", zero)
        mtd_chf = cf.mtd.get("CHF", zero)
        bal_usd = cf.bal.get("USD", zero)
        bal_chf = cf.bal.get("CHF", zero)
        lines.append(
            f"- Today (今日): {today_usd:+,.2f} USD | {today_chf:+,.2f} CHF"
        )
        lines.append(
            f"- MTD (本月累計): {mtd_usd:+,.2f} USD | {mtd_chf:+,.2f} CHF"
        )
        lines.append(
            f"- Bal (餘額): USD {bal_usd:,.2f} | CHF {bal_chf:,.2f}"
        )
    lines.append("")

    gaps: list[str] = []
    if d.news is None:
        gaps.append("News")
    if d.fx is None:
        gaps.append("FX")
    if d.cashflow is None:
        gaps.append("Ledger")
    if d.snapshot is None:
        gaps.append("Positions")

    if gaps or d.exceptions:
        lines.append("\U0001f9fe Notes (備註)")
        if gaps:
            lines.append(
                f"- Data gaps (資料缺漏): {', '.join(gaps)} unavailable"
            )
        if d.exceptions:
            lines.append(f"- Exceptions (異常): {'; '.join(d.exceptions)}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
