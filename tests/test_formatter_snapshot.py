from datetime import date
from decimal import Decimal
from pathlib import Path

from src.formatter import render
from src.models import (
    Cashflow,
    CashflowEvent,
    Coupon,
    DigestInput,
    FxResult,
    HoldingPrice,
    NewsItem,
    Projection,
    Snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _all_populated() -> DigestInput:
    return DigestInput(
        date_str="2026-04-25",
        news=[
            NewsItem(
                "ACME",
                "Q1 results in line with guidance",
                "acme.com",
                link="https://acme.com/q1",
            ),
            NewsItem(
                "BETA",
                "New CFO appointed",
                "reuters.com",
                link="https://reuters.com/beta-cfo",
            ),
        ],
        fx=FxResult(
            usd_chf=Decimal("0.9123"),
            chf_usd=Decimal("1.0961"),
            usd_chf_dod_pct=Decimal("-0.12"),
        ),
        snapshot=Snapshot(
            total_cost={"USD": Decimal("14750.00")},
            annual_coupon={"USD": Decimal("760.00")},
            next_coupon=Coupon(
                issuer="ACME",
                date=date(2026, 5, 1),
                amount=Decimal("27.50"),
                currency="USD",
            ),
            uncosted_issuers=[],
            prices=[
                HoldingPrice(
                    name="Issuer Alpha",
                    currency="USD",
                    buy_price=Decimal("98.50"),
                    current_price=Decimal("99.2500"),
                    as_of=date(2026, 4, 24),
                    change_pct=Decimal("0.76"),
                    pnl=Decimal("75.00"),
                ),
                HoldingPrice(
                    name="Issuer Beta",
                    currency="USD",
                    buy_price=Decimal("99.00"),
                ),
            ],
            unrealized={"USD": Decimal("75.00")},
            price_as_of_min=date(2026, 4, 24),
            price_as_of_max=date(2026, 4, 24),
        ),
        cashflow=Cashflow(
            today={"USD": Decimal("55.00"), "CHF": Decimal("-12.50")},
            mtd={"USD": Decimal("1065.00"), "CHF": Decimal("197.50")},
            bal={"USD": Decimal("1065.00"), "CHF": Decimal("197.50")},
        ),
        exceptions=[],
        projected=Projection(
            events=[
                CashflowEvent(
                    date=date(2026, 5, 1),
                    label="ACME 配息",
                    amount=Decimal("27.50"),
                    currency="USD",
                    category="coupon",
                ),
                CashflowEvent(
                    date=date(2026, 6, 4),
                    label="Repo interest",
                    amount=Decimal("-40.00"),
                    currency="CHF",
                    category="interest",
                    is_estimate=True,
                ),
            ],
            horizon_days=60,
            net={"USD": Decimal("27.50"), "CHF": Decimal("-40.00")},
            next_inflow=CashflowEvent(
                date=date(2026, 5, 1),
                label="ACME 配息",
                amount=Decimal("27.50"),
                currency="USD",
                category="coupon",
            ),
            next_inflow_days=6,
        ),
    )


def _all_gaps() -> DigestInput:
    return DigestInput(
        date_str="2026-04-25",
        news=None,
        fx=None,
        snapshot=None,
        cashflow=None,
        exceptions=["ledger_csv_parse_error: 2 rows skipped"],
    )


def test_render_all_populated_matches_golden():
    expected = (FIXTURES / "expected_all_populated.txt").read_text(encoding="utf-8")
    assert render(_all_populated()) == expected


def test_render_all_gaps_matches_golden():
    expected = (FIXTURES / "expected_all_gaps.txt").read_text(encoding="utf-8")
    assert render(_all_gaps()) == expected


def test_status_flips_to_alert_when_news_item_is_alert():
    d = _all_populated()
    flagged = NewsItem("ACME", "Issuer downgrade by S&P", "reuters.com", is_alert=True)
    d_alert = DigestInput(
        date_str=d.date_str,
        news=[flagged],
        fx=d.fx,
        snapshot=d.snapshot,
        cashflow=d.cashflow,
        exceptions=d.exceptions,
    )
    out = render(d_alert)
    assert "⚠️ 狀態：警報" in out
    assert "✅ 狀態：一切正常" not in out


def test_est_annual_coupon_omits_yield_when_cost_currency_is_missing():
    """Yield % is only meaningful when there's a cost basis to divide by.
    A currency that has annual_coupon but no matching total_cost (e.g., a
    portfolio of uncosted FCN positions) must not render '0.00% yield' or
    crash on a div-by-zero — just show the coupon and skip the suffix."""
    base = _all_populated()
    snap_no_cost = Snapshot(
        total_cost={},  # no cost basis at all
        annual_coupon={"USD": Decimal("760.00")},
        next_coupon=base.snapshot.next_coupon,
        uncosted_issuers=["FCN (TSM, BRK)"],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap_no_cost,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "預估年息：760.00 USD" in out
    assert "yield" not in out
    assert "殖利率" not in out


def test_est_annual_coupon_omits_yield_when_total_cost_is_zero():
    """Same guard for explicit-zero cost: divide-by-zero would be misleading
    even if computable (Decimal would raise InvalidOperation)."""
    base = _all_populated()
    snap_zero_cost = Snapshot(
        total_cost={"USD": Decimal("0.00")},
        annual_coupon={"USD": Decimal("760.00")},
        next_coupon=base.snapshot.next_coupon,
        uncosted_issuers=[],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap_zero_cost,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "預估年息：760.00 USD" in out
    assert "yield" not in out


def test_fx_line_appends_as_of_when_rate_is_stale():
    """If the Frankfurter rate is dated to a prior business day (weekend,
    holiday, or pre-CET-noon weekday digest), surface that on the USD/CHF
    line so the reader doesn't think it's today's market move."""
    base = _all_populated()
    stale_fx = FxResult(
        usd_chf=base.fx.usd_chf,
        chf_usd=base.fx.chf_usd,
        usd_chf_dod_pct=base.fx.usd_chf_dod_pct,
        as_of=date(2026, 4, 24),  # date_str is 2026-04-25 → stale by one day
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=stale_fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "USD/CHF: 0.9123（較前日 -0.12%，資料日期 2026-04-24）" in out


def test_fx_line_omits_as_of_when_rate_matches_today():
    """No staleness annotation when as_of matches the digest's date."""
    base = _all_populated()
    fresh_fx = FxResult(
        usd_chf=base.fx.usd_chf,
        chf_usd=base.fx.chf_usd,
        usd_chf_dod_pct=base.fx.usd_chf_dod_pct,
        as_of=date(2026, 4, 25),  # matches date_str
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=fresh_fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "USD/CHF: 0.9123（較前日 -0.12%）" in out
    assert "資料日期" not in out


def test_news_never_renders_links_or_sources():
    """Links made the message several phone-screens long; sources add nothing
    once the summary is in Chinese. Neither may reach the group."""
    base = _all_populated()
    linked = NewsItem(
        "ACME",
        "Q1 results in line with guidance",
        "acme.com",
        link="https://acme.com/q1",
    )
    d = DigestInput(
        date_str=base.date_str,
        news=[linked],
        fx=base.fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "- ACME: Q1 results in line with guidance" in out
    assert "https://" not in out
    assert "acme.com" not in out


def test_news_overview_replaces_the_item_list_entirely():
    """With LLM enrichment the section is one paragraph, not a per-item list."""
    base = _all_populated()
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
        news_overview="今日新聞對本組合無重大影響，僅 ACME 公布財報符合預期。",
    )
    out = render(d)
    assert "今日新聞對本組合無重大影響，僅 ACME 公布財報符合預期。" in out
    # The individual headlines must not also appear.
    assert "New CFO appointed" not in out
    assert "Q1 results in line with guidance" not in out


def test_next_coupon_renders_non_usd_currency():
    base = _all_populated()
    chf_coupon = Coupon(
        issuer="Issuer X",
        date=date(2026, 5, 1),
        amount=Decimal("100.00"),
        currency="CHF",
    )
    snap = Snapshot(
        total_cost=base.snapshot.total_cost,
        annual_coupon=base.snapshot.annual_coupon,
        next_coupon=chf_coupon,
        uncosted_issuers=[],
    )
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
    )
    out = render(d)
    assert "- 七日內下個利息：Issuer X 2026-05-01 100.00 CHF" in out


def test_greeting_renders_directly_under_title():
    base = _all_populated()
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
        projected=base.projected,
        greeting="☀️ 早安！今天星期六 🧡\n加油加油 💪\n😄 今日笑話：冷冷的",
    )
    out = render(d)
    lines = out.splitlines()
    assert lines[0].startswith("【每日投資摘要】")
    assert lines[2] == "☀️ 早安！今天星期六 🧡"
    assert lines[4] == "😄 今日笑話：冷冷的"
    assert lines[5] == ""
    assert "狀態" in lines[6]


def test_no_greeting_keeps_legacy_layout():
    out = render(_all_populated())
    assert "早安" not in out


def _with_projection(p: Projection) -> DigestInput:
    base = _all_populated()
    return DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
        projected=p,
    )


def test_next_inflow_shown_even_when_horizon_is_empty():
    out = render(
        _with_projection(
            Projection(
                events=[],
                horizon_days=60,
                net={},
                next_inflow=CashflowEvent(
                    date=date(2026, 11, 15),
                    label="陶氏化學 配息",
                    amount=Decimal("6157.00"),
                    currency="USD",
                    category="coupon",
                ),
                next_inflow_days=204,
            )
        )
    )
    assert (
        "- 💵 下次進帳：2026-11-15 +6,157.00 USD 陶氏化學 配息 (還有 204 天)"
        in out
    )
    # Seven months out, the "money lands about a week later" note is noise.
    assert "入到銀行帳戶" not in out


def test_bank_lag_note_returns_when_the_coupon_is_days_away():
    out = render(
        _with_projection(
            Projection(
                events=[],
                horizon_days=60,
                net={},
                next_inflow=CashflowEvent(
                    date=date(2026, 4, 29),
                    label="陶氏化學 配息",
                    amount=Decimal("6157.00"),
                    currency="USD",
                    category="coupon",
                ),
                next_inflow_days=4,
            )
        )
    )
    assert "- 註：債券配息通常於配息日後約一週入到銀行帳戶" in out


def test_projection_calendar_is_not_rendered_at_all():
    """The dated 60-day calendar was dropped: a dozen lines that hardly moved
    day to day. Its events, nets and header must not come back."""
    out = render(_all_populated())
    assert "預估現金流" not in out
    assert "Repo interest" not in out
    assert "60 天淨額" not in out
    assert "2026-06-04" not in out


def test_next_inflow_today_reads_as_today_not_zero_days():
    out = render(
        _with_projection(
            Projection(
                events=[],
                horizon_days=60,
                net={},
                next_inflow=CashflowEvent(
                    date=date(2026, 4, 25),
                    label="南山人壽 配息",
                    amount=Decimal("83481.00"),
                    currency="USD",
                    category="insurance",
                    is_estimate=True,
                ),
                next_inflow_days=0,
            )
        )
    )
    assert (
        "- 💵 下次進帳：2026-04-25 +83,481.00 USD 南山人壽 配息 (預估) (今天)"
        in out
    )


def test_no_next_inflow_line_when_nothing_comes_in():
    out = render(
        _with_projection(Projection(events=[], horizon_days=60, net={}))
    )
    assert "下次進帳" not in out


def _with_snapshot(snap: Snapshot) -> DigestInput:
    base = _all_populated()
    return DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=base.fx,
        snapshot=snap,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
        projected=base.projected,
    )


def _snapshot_with_prices(**kwargs) -> Snapshot:
    base = _all_populated().snapshot
    fields = {
        "total_cost": base.total_cost,
        "annual_coupon": base.annual_coupon,
        "next_coupon": base.next_coupon,
        "prices": base.prices,
        "unrealized": base.unrealized,
        "price_as_of_min": base.price_as_of_min,
        "price_as_of_max": base.price_as_of_max,
    }
    fields.update(kwargs)
    return Snapshot(**fields)


def test_price_line_renders_direction_and_entry_price():
    out = render(_all_populated())
    assert "- Issuer Alpha：99.25 ▲ 0.76%（入手 98.50）" in out


def test_price_below_entry_renders_a_down_arrow_and_positive_magnitude():
    """The arrow carries the sign, so the number itself reads as a magnitude —
    '▼ -1.21%' would say the move twice and invite a double negative."""
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="陶氏化學",
                currency="USD",
                buy_price=Decimal("132.7100"),
                current_price=Decimal("131.1000"),
                as_of=date(2026, 4, 24),
                change_pct=Decimal("-1.21"),
                pnl=Decimal("-2109.10"),
            )
        ],
        unrealized={"USD": Decimal("-2109.10")},
    )
    out = render(_with_snapshot(snap))
    assert "- 陶氏化學：131.10 ▼ 1.21%（入手 132.71）" in out
    assert "- 合計未實現：-2,109.10 USD" in out


def test_unchanged_price_reads_as_flat():
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="Issuer Alpha",
                currency="USD",
                buy_price=Decimal("98.50"),
                current_price=Decimal("98.50"),
                as_of=date(2026, 4, 24),
                change_pct=Decimal("0.00"),
                pnl=Decimal("0.00"),
            )
        ],
        unrealized={"USD": Decimal("0.00")},
    )
    out = render(_with_snapshot(snap))
    assert "- Issuer Alpha：98.50 持平（入手 98.50）" in out


def test_three_decimal_quotes_are_not_rounded_away():
    """Statements quote some of these to 3-4dp; rewriting 101.715 as 101.72
    would break reconciliation against the broker's own numbers."""
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="聯合健康",
                currency="USD",
                buy_price=Decimal("101.7150"),
                current_price=Decimal("102.1250"),
                as_of=date(2026, 4, 24),
                change_pct=Decimal("0.40"),
                pnl=Decimal("6150.00"),
            )
        ],
        unrealized={"USD": Decimal("6150.00")},
    )
    out = render(_with_snapshot(snap))
    assert "- 聯合健康：102.125 ▲ 0.40%（入手 101.715）" in out


def test_stale_quote_file_says_how_long_it_has_been_sitting():
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 3, 20),
        price_as_of_max=date(2026, 3, 20),
    )
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（報價日 2026-03-20，已 36 天未更新）" in out


def test_future_quote_date_is_flagged_as_anomaly():
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 4, 27),
        price_as_of_max=date(2026, 4, 27),
    )
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（報價日 2026-04-27，日期異常：晚於摘要 2 天）" in out


def test_mixed_quote_dates_render_as_a_span():
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 4, 22),
        price_as_of_max=date(2026, 4, 24),
    )
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（報價日 2026-04-22 ~ 2026-04-24）" in out


def test_mixed_quote_dates_flag_the_oldest_stale_quote():
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 3, 20),
        price_as_of_max=date(2026, 4, 24),
    )
    out = render(_with_snapshot(snap))
    assert (
        "📈 持倉行情（報價日 2026-03-20 ~ 2026-04-24，最舊已 36 天未更新）"
        in out
    )


def test_undated_quote_is_disclosed_alongside_dated_quotes():
    dated = _all_populated().snapshot.prices[0]
    undated = HoldingPrice(
        name="Issuer Beta",
        currency="USD",
        buy_price=Decimal("99.00"),
        current_price=Decimal("100.00"),
    )
    snap = _snapshot_with_prices(prices=[dated, undated])
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（報價日 2026-04-24，另有 1 檔日期不明）" in out


def test_all_undated_quotes_are_disclosed_in_header():
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="Issuer Alpha",
                currency="USD",
                buy_price=Decimal("98.50"),
                current_price=Decimal("99.25"),
            )
        ],
        price_as_of_min=None,
        price_as_of_max=None,
    )
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（1 檔報價日期不明）" in out


def test_unrealized_total_flags_partial_coverage():
    """A total that silently skips unquoted holdings would overstate how much
    of the book it covers."""
    out = render(_all_populated())
    assert "- 合計未實現：+75.00 USD（未含 1 檔無報價）" in out


def test_unrealized_total_drops_the_caveat_when_everything_is_quoted():
    snap = _snapshot_with_prices(prices=[_all_populated().snapshot.prices[0]])
    out = render(_with_snapshot(snap))
    assert "- 合計未實現：+75.00 USD" in out
    assert "未含" not in out


def test_missing_quote_file_is_a_reported_data_gap():
    """Every bond unquoted means prices.csv is absent or empty — that has to
    show up in Notes rather than looking like a flat market."""
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="Issuer Alpha",
                currency="USD",
                buy_price=Decimal("98.50"),
            )
        ],
        unrealized={},
        price_as_of_min=None,
        price_as_of_max=None,
    )
    out = render(_with_snapshot(snap))
    assert "- Issuer Alpha：無報價（入手 98.50）" in out
    assert "- 資料缺漏：報價" in out
    assert "合計未實現" not in out


def test_quote_without_a_known_entry_price_still_renders():
    snap = _snapshot_with_prices(
        prices=[
            HoldingPrice(
                name="Sample FCN",
                currency="USD",
                current_price=Decimal("100.0000"),
                as_of=date(2026, 4, 24),
            )
        ],
        unrealized={},
    )
    out = render(_with_snapshot(snap))
    assert "- Sample FCN：100.00（入手價不明）" in out


def test_twd_line_renders_after_the_franc_pair():
    base = _all_populated()
    d = DigestInput(
        date_str=base.date_str,
        news=base.news,
        fx=FxResult(
            usd_chf=Decimal("0.9123"),
            chf_usd=Decimal("1.0961"),
            usd_chf_dod_pct=Decimal("-0.12"),
            usd_twd=Decimal("31.8477"),
        ),
        snapshot=base.snapshot,
        cashflow=base.cashflow,
        exceptions=base.exceptions,
        projected=base.projected,
    )
    out = render(d)
    assert "- USD/TWD: 31.8477" in out
    assert out.index("CHF/USD") < out.index("USD/TWD")


def test_twd_line_is_omitted_when_the_rate_is_unavailable():
    """No DoD% is available for TWD, so a placeholder row with a blank change
    would read as 'unchanged' rather than 'missing'. Drop the line instead."""
    out = render(_all_populated())
    assert "USD/TWD" not in out
    assert "USD/CHF" in out


def test_a_long_weekend_behind_the_oldest_source_is_not_called_stale():
    """Staleness is judged from the oldest quote, and one of the sources is a
    bank table that does not move at weekends. A Friday quote read on the
    Tuesday after a long weekend is four days old and entirely healthy —
    crying stale every Tuesday would train everyone to ignore the warning."""
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 4, 21),  # four days before the digest
        price_as_of_max=date(2026, 4, 25),
    )
    out = render(_with_snapshot(snap))
    assert "📈 持倉行情（報價日 2026-04-21 ~ 2026-04-25）" in out
    assert "未更新" not in out


def test_one_day_past_the_weekend_allowance_does_flag():
    snap = _snapshot_with_prices(
        price_as_of_min=date(2026, 4, 20),  # five days
        price_as_of_max=date(2026, 4, 25),
    )
    out = render(_with_snapshot(snap))
    assert "最舊已 5 天未更新" in out
