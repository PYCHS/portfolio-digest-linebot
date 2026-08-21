from __future__ import annotations

from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal

from .models import DigestInput, HoldingPrice

YIELD_QUANTUM = Decimal("0.01")
# A quote file nobody has refreshed for over a week is still worth showing,
# but it must not read as today's market — past this many days the header
# says how long it has been sitting.
PRICE_STALE_DAYS = 7
# The "coupons reach the bank about a week later" note only earns its line in
# the run-up to an actual payment, not on the other ~355 days of the year.
BANK_LAG_NOTE_DAYS = 7


def _fmt_price(p: Decimal) -> str:
    """Render a clean price, keeping the precision the statement gave.

    Quotes come in at two, three, or four decimals (94.67, 101.715); forcing
    a fixed 2dp would silently rewrite the entry prices people reconcile
    against.
    """
    out = f"{p:,.4f}"
    while out.endswith("0") and len(out.split(".")[1]) > 2:
        out = out[:-1]
    return out


def _fmt_change(change_pct: Decimal) -> str:
    if change_pct > 0:
        return f"▲ {change_pct:.2f}%"
    if change_pct < 0:
        return f"▼ {abs(change_pct):.2f}%"
    return "持平"


def _price_line(h: HoldingPrice) -> str:
    if h.current_price is None:
        tail = f"（入手 {_fmt_price(h.buy_price)}）" if h.buy_price is not None else ""
        return f"- {h.name}：無報價{tail}"
    if h.buy_price is None:
        return f"- {h.name}：{_fmt_price(h.current_price)}（入手價不明）"
    change = f" {_fmt_change(h.change_pct)}" if h.change_pct is not None else ""
    return (
        f"- {h.name}：{_fmt_price(h.current_price)}{change}"
        f"（入手 {_fmt_price(h.buy_price)}）"
    )


def _price_header(d: DigestInput) -> str:
    """Section title, annotated with the quote date(s) behind the numbers."""
    title = "\U0001f4c8 持倉行情"
    s = d.snapshot
    if s is None or s.price_as_of_max is None:
        return title
    newest = s.price_as_of_max
    if s.price_as_of_min is not None and s.price_as_of_min != newest:
        span = f"{s.price_as_of_min.isoformat()} ~ {newest.isoformat()}"
    else:
        span = newest.isoformat()
    try:
        age = (Date.fromisoformat(d.date_str) - newest).days
    except ValueError:
        age = 0
    if age < 0:
        freshness = f"，日期異常：晚於摘要 {abs(age)} 天"
    elif age > PRICE_STALE_DAYS:
        freshness = f"，已 {age} 天未更新"
    else:
        freshness = ""
    return f"{title}（報價日 {span}{freshness}）"


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
        # The dated projection calendar was dropped: a dozen lines that barely
        # moved day to day. "When does money next arrive?" is the one question
        # it actually answered, so that single line survives here.
        if d.projected is not None and d.projected.next_inflow is not None:
            nx = d.projected.next_inflow
            est = " (預估)" if nx.is_estimate else ""
            days = d.projected.next_inflow_days
            if days is None:
                tail = ""
            elif days == 0:
                tail = " (今天)"
            else:
                tail = f" (還有 {days} 天)"
            lines.append(
                f"- 💵 下次進帳：{nx.date.isoformat()} "
                f"{nx.amount:+,.2f} {nx.currency} {nx.label}{est}{tail}"
            )
            if (
                nx.category == "coupon"
                and days is not None
                and days <= BANK_LAG_NOTE_DAYS
            ):
                lines.append("- 註：債券配息通常於配息日後約一週入到銀行帳戶")
    lines.append("")

    lines.append(_price_header(d))
    if d.snapshot is None:
        lines.append("- 無資料")
    elif not d.snapshot.prices:
        lines.append("- 無報價資料")
    else:
        s = d.snapshot
        for h in s.prices:
            lines.append(_price_line(h))
        # Flagged rather than hidden: a total that quietly skips two unquoted
        # bonds is worse than no total at all.
        unquoted = sum(1 for h in s.prices if h.current_price is None)
        suffix = f"（未含 {unquoted} 檔無報價）" if unquoted else ""
        for ccy in sorted(s.unrealized):
            lines.append(f"- 合計未實現：{s.unrealized[ccy]:+,.2f} {ccy}{suffix}")
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
    elif not any(h.current_price is not None for h in d.snapshot.prices):
        gaps.append("報價")
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
