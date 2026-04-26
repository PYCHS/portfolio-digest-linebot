from __future__ import annotations

from .models import DigestInput


def render(d: DigestInput) -> str:
    lines: list[str] = []
    lines.append(f"【Daily Investment Digest】{d.date_str} (Asia/Taipei)")
    lines.append("")

    is_alert = bool(d.news) and any(n.is_alert for n in d.news)
    lines.append("⚠️ Status: Alert" if is_alert else "✅ Status: All clear")
    lines.append("")

    lines.append("\U0001f4f0 News (last 24h, 0–1 per issuer)")
    if d.news is None:
        lines.append("- (unavailable)")
    elif not d.news:
        lines.append("- (no items)")
    else:
        for n in d.news:
            lines.append(f"- {n.issuer_id}: {n.summary} ({n.source})")
    lines.append("")

    lines.append("\U0001f4b1 FX")
    if d.fx is None:
        lines.append("- (unavailable)")
    else:
        if d.fx.usd_chf_dod_pct is None:
            dod = "Δ N/A"
        else:
            dod = f"Δ {d.fx.usd_chf_dod_pct:+.2f}% DoD"
        lines.append(f"- USD/CHF: {d.fx.usd_chf:.4f} ({dod})")
        lines.append(f"- CHF/USD: {d.fx.chf_usd:.4f}")
    lines.append("")

    lines.append("\U0001f4cc Portfolio Snapshot (Cost-based)")
    if d.snapshot is None:
        lines.append("- (unavailable)")
    else:
        s = d.snapshot
        for ccy in sorted(s.total_cost):
            lines.append(f"- Total Cost: {s.total_cost[ccy]:,.2f} {ccy}")
        for ccy in sorted(s.annual_coupon):
            lines.append(f"- Est. Annual Coupon: {s.annual_coupon[ccy]:,.2f} {ccy}")
        if s.next_coupon is None:
            lines.append("- Next Coupon (7d): none")
        else:
            c = s.next_coupon
            lines.append(
                f"- Next Coupon (7d): {c.issuer} {c.date.isoformat()} "
                f"{c.amount:,.2f} {c.currency}"
            )
        for issuer in s.uncosted_issuers:
            lines.append(f"- {issuer}: Cost unavailable")
    lines.append("")

    lines.append("\U0001f4b0 Cashflow (Ledger)")
    if d.cashflow is None:
        lines.append("- (unavailable)")
    else:
        cf = d.cashflow
        lines.append(f"- Today: {cf.today_usd:+,.2f} USD | {cf.today_chf:+,.2f} CHF")
        lines.append(f"- MTD:   {cf.mtd_usd:+,.2f} USD | {cf.mtd_chf:+,.2f} CHF")
        lines.append(f"- Bal:   USD {cf.bal_usd:,.2f} | CHF {cf.bal_chf:,.2f}")
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
        lines.append("\U0001f9fe Notes")
        if gaps:
            lines.append(f"- Data gaps: {', '.join(gaps)} unavailable")
        if d.exceptions:
            lines.append(f"- Exceptions: {'; '.join(d.exceptions)}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
