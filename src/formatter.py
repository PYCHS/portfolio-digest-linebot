from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import DigestInput

YIELD_QUANTUM = Decimal("0.01")


def render(d: DigestInput) -> str:
    lines: list[str] = []
    lines.append(f"【每日投資摘要】{d.date_str}（台北時間）")
    lines.append("")

    if d.greeting:
        lines.extend(d.greeting.splitlines())
        lines.append("")

    is_alert = bool(d.news) and any(n.is_alert for n in d.news)
    lines.append("⚠️ 狀態：警報" if is_alert else "✅ 狀態：一切正常")
    lines.append("")

    lines.append("\U0001f4b1 匯率")
    if d.fx is None:
        lines.append("- 無資料")
    else:
        if d.fx.usd_chf_dod_pct is None:
            dod = "較前日 無資料"
        else:
            dod = f"較前日 {d.fx.usd_chf_dod_pct:+.2f}%"
        # Surface staleness when the rate's as-of date isn't today (Taipei).
        # Frankfurter publishes weekday rates around 15:00 CET, so a digest
        # fired before then — or on a weekend / holiday — gets an earlier
        # business day's rate; saying so up front avoids "is this today's
        # number?" confusion.
        if d.fx.as_of and d.fx.as_of.isoformat() != d.date_str:
            stale = f"，資料日期 {d.fx.as_of.isoformat()}"
        else:
            stale = ""
        lines.append(f"- USD/CHF: {d.fx.usd_chf:.4f}（{dod}{stale}）")
        lines.append(f"- CHF/USD: {d.fx.chf_usd:.4f}")
    lines.append("")

    lines.append("\U0001f4f0 新聞")
    if d.news is None:
        lines.append("- 無資料")
    elif not d.news:
        lines.append("- 今日無相關新聞")
    elif d.news_overview:
        # One consolidated paragraph rather than a per-item list: the family
        # reads this on a phone, and the links made it several screens long.
        lines.append(d.news_overview)
    else:
        # No LLM enrichment available — fall back to bare headlines, still
        # without links.
        for n in d.news:
            lines.append(f"- {n.issuer_id}: {n.summary}")
    lines.append("")

    lines.append("\U0001f4cc 投資組合快照（依成本計）")
    if d.snapshot is None:
        lines.append("- 無資料")
    else:
        s = d.snapshot
        for ccy in sorted(s.total_cost):
            lines.append(f"- 總成本：{s.total_cost[ccy]:,.2f} {ccy}")
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
                suffix = f"（殖利率 {yield_pct:.2f}%）"
            else:
                suffix = ""
            lines.append(f"- 預估年息：{coupon:,.2f} {ccy}{suffix}")
        if s.next_coupon is None:
            lines.append("- 七日內下個利息：無")
        else:
            c = s.next_coupon
            lines.append(
                f"- 七日內下個利息：{c.issuer} {c.date.isoformat()} "
                f"{c.amount:,.2f} {c.currency}"
            )
        for issuer in s.uncosted_issuers:
            lines.append(f"- {issuer}：成本不明")
    lines.append("")

    lines.append("\U0001f4c5 預估現金流")
    if d.projected is None:
        lines.append("- 無資料")
    else:
        p = d.projected
        has_coupon_inflow = False
        if not p.events:
            lines.append(f"- 未來 {p.horizon_days} 天：無")
        else:
            for e in p.events:
                mark = "💸" if e.category == "interest" else ""
                est = " (預估)" if e.is_estimate else ""
                lines.append(
                    f"- {e.date.isoformat()} {e.amount:+,.2f} {e.currency}"
                    f" {mark}{e.label}{est}"
                )
                if e.category == "coupon" and e.amount > 0:
                    has_coupon_inflow = True
            net_parts = [
                f"{p.net[ccy]:+,.2f} {ccy}" for ccy in sorted(p.net)
            ]
            lines.append(
                f"- {p.horizon_days} 天淨額：{' | '.join(net_parts)}"
            )
        # Rendered even when the horizon is empty — that is exactly the case
        # where "when does money next arrive?" needs answering.
        if p.next_inflow is not None:
            nx = p.next_inflow
            est = " (預估)" if nx.is_estimate else ""
            if p.next_inflow_days is None:
                tail = ""
            elif p.next_inflow_days == 0:
                tail = " (今天)"
            else:
                tail = f" (還有 {p.next_inflow_days} 天)"
            lines.append(
                f"- 💵 下次進帳：{nx.date.isoformat()} "
                f"{nx.amount:+,.2f} {nx.currency} {nx.label}{est}{tail}"
            )
            if nx.category == "coupon":
                has_coupon_inflow = True
        if has_coupon_inflow:
            lines.append("- 註：債券配息通常於配息日後約一週入到銀行帳戶")
    lines.append("")

    lines.append("\U0001f4b0 現金流（帳本）")
    if d.cashflow is None:
        lines.append("- 無資料")
    else:
        cf = d.cashflow
        zero = Decimal("0.00")
        today_usd = cf.today.get("USD", zero)
        today_chf = cf.today.get("CHF", zero)
        mtd_usd = cf.mtd.get("USD", zero)
        mtd_chf = cf.mtd.get("CHF", zero)
        bal_usd = cf.bal.get("USD", zero)
        bal_chf = cf.bal.get("CHF", zero)
        lines.append(f"- 今日：{today_usd:+,.2f} USD | {today_chf:+,.2f} CHF")
        lines.append(f"- 本月累計：{mtd_usd:+,.2f} USD | {mtd_chf:+,.2f} CHF")
        lines.append(f"- 餘額：USD {bal_usd:,.2f} | CHF {bal_chf:,.2f}")
    lines.append("")

    gaps: list[str] = []
    if d.news is None:
        gaps.append("新聞")
    if d.fx is None:
        gaps.append("匯率")
    if d.cashflow is None:
        gaps.append("帳本")
    if d.snapshot is None:
        gaps.append("持倉")
    if d.projected is None:
        gaps.append("現金流預估")

    if gaps or d.exceptions:
        lines.append("\U0001f9fe 備註")
        if gaps:
            lines.append(f"- 資料缺漏：{'、'.join(gaps)}")
        if d.exceptions:
            lines.append(f"- 異常：{'; '.join(d.exceptions)}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
