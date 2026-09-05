from datetime import date
from decimal import Decimal
from pathlib import Path

from src.sources.schedule import project_cashflows

TODAY = date(2026, 8, 4)

POSITIONS_HEADER = (
    "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
    "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
    "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
)
RECURRING_HEADER = (
    "label,currency,amount,schedule,start_date,end_date,category,estimate\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_files_is_clean_empty_projection(tmp_path):
    proj, exc = project_cashflows(
        tmp_path / "nope.csv", tmp_path / "nope2.csv", TODAY
    )
    assert exc == []
    assert proj is not None
    assert proj.events == []
    assert proj.net == {}


def test_invalid_recurring_encoding_does_not_discard_position_events(tmp_path):
    positions = _write(
        tmp_path,
        "positions.csv",
        POSITIONS_HEADER
        + "bond,Valid Bond,US91324PFK30,,1000,5,2044,100,100000,1000,500,5,5,8/8\n",
    )
    recurring = tmp_path / "recurring.csv"
    recurring.write_bytes(b"\xff\xfe")

    proj, exc = project_cashflows(positions, recurring, TODAY)

    assert [e.label for e in proj.events] == ["Valid Bond 配息"]
    assert len(exc) == 1
    assert exc[0].startswith("recurring: read error:")


def test_invalid_positions_encoding_does_not_discard_recurring_events(tmp_path):
    positions = tmp_path / "positions.csv"
    positions.write_bytes(b"\xff\xfe")
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER + "Valid payout,USD,10,once:2026-08-08,,,other,0\n",
    )

    proj, exc = project_cashflows(positions, recurring, TODAY)

    assert [e.label for e in proj.events] == ["Valid payout"]
    assert len(exc) == 1
    assert exc[0].startswith("schedule: positions read error:")


def test_bond_coupons_expand_from_positions(tmp_path):
    positions = _write(
        tmp_path,
        "positions.csv",
        POSITIONS_HEADER
        + "bond,UNH 5.5% 2044,US91324PFK30,,1500000,5.50,2044,"
        "101.7150,1525725.00,82500.00,41250.00,5.33,5.41,1/15;7/15\n"
        + "bond,PM 4.875% 2043,US718172BD03,,900000,4.875,2043,"
        "94.6700,852030.00,43875.00,21937.50,5.32,5.15,5/15;11/15\n",
    )
    # 60d window from Aug 4 ends Oct 3 — no coupons for either bond.
    proj, exc = project_cashflows(positions, tmp_path / "nope.csv", TODAY)
    assert exc == []
    assert proj.events == []

    # Widen to 120d — PM pays 11/15 inside the window; UNH (1/15) does not.
    proj, exc = project_cashflows(
        positions, tmp_path / "nope.csv", TODAY, horizon_days=120
    )
    assert [e.label for e in proj.events] == ["PM 4.875% 2043 配息"]
    e = proj.events[0]
    assert e.date == date(2026, 11, 15)
    assert e.amount == Decimal("21937.50")
    assert e.currency == "USD"
    assert e.category == "coupon"
    assert not e.is_estimate


def test_partially_malformed_coupon_schedule_is_not_projected(tmp_path):
    positions = _write(
        tmp_path,
        "positions.csv",
        POSITIONS_HEADER
        + "bond,Bad Schedule,US91324PFK30,,1000,5.50,2044,"
        "100.00,100000.00,1000.00,500.00,5.50,5.50,9/15;bad\n",
    )

    proj, exc = project_cashflows(positions, tmp_path / "nope.csv", TODAY)

    assert proj.events == []
    assert exc == ["schedule: positions row 2: bad coupon date 'bad'"]


def test_duplicate_coupon_dates_do_not_create_multiple_payments(tmp_path):
    positions = _write(
        tmp_path, "positions.csv", POSITIONS_HEADER
        + "bond,Duplicate Dates,US91324PFK30,,1000,5,2044,"
        "100,100000,1000,500,5,5,9/15;09/15\n",
    )
    proj, exc = project_cashflows(positions, tmp_path / "nope.csv", TODAY)
    assert proj.events == []
    assert proj.next_inflow is None
    assert exc == ["schedule: positions row 2: duplicate coupon date '09/15'"]


def test_recurring_monthly_with_end_date(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "FCN monthly coupon,USD,1016.67,monthly:8,2026-06-08,2026-10-08,coupon,0\n",
    )
    proj, exc = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY, horizon_days=90
    )
    assert exc == []
    # Window Aug 4 – Nov 2, but the row ends 10/8: expect 8/8, 9/8, 10/8.
    assert [e.date for e in proj.events] == [
        date(2026, 8, 8),
        date(2026, 9, 8),
        date(2026, 10, 8),
    ]
    assert proj.net == {"USD": Decimal("3050.01")}


def test_recurring_estimate_flag_is_case_insensitive(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Estimated payout,USD,500.00,once:2026-08-08,,,insurance,TRUE\n",
    )

    proj, exc = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)

    assert exc == []
    assert len(proj.events) == 1
    assert proj.events[0].is_estimate is True


def test_unknown_recurring_estimate_flag_is_conservatively_marked(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Typo payout,USD,500.00,once:2026-08-08,,,insurance,treu\n",
    )

    proj, exc = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)

    assert len(proj.events) == 1
    assert proj.events[0].is_estimate is True
    assert exc == [
        "recurring row 2: unknown estimate flag 'treu'; treating as estimate"
    ]


def test_recurring_yearly_quarter_dates_and_once(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Repo interest,CHF,-4329.11,yearly:3/4;6/4;9/4;12/4,2026-06-04,,interest,1\n"
        + "FCN redemption,USD,100000.00,once:2026-10-08,,,principal,1\n",
    )
    proj, exc = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY, horizon_days=70
    )
    assert exc == []
    assert [(e.date, e.currency) for e in proj.events] == [
        (date(2026, 9, 4), "CHF"),
        (date(2026, 10, 8), "USD"),
    ]
    interest = proj.events[0]
    assert interest.category == "interest"
    assert interest.is_estimate
    assert proj.net == {"CHF": Decimal("-4329.11"), "USD": Decimal("100000.00")}


def test_recurring_bad_rows_reported_but_isolated(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Bad amount,USD,notanumber,monthly:8,,,coupon,0\n"
        + "Bad schedule,USD,10.00,weekly:1,,,coupon,0\n"
        + "Good,USD,10.00,monthly:8,,,coupon,0\n",
    )
    proj, exc = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY, horizon_days=30
    )
    assert len(exc) == 2
    assert any("row 2" in e for e in exc)
    assert any("row 3" in e for e in exc)
    assert [e.label for e in proj.events] == ["Good"]


def test_reversed_recurring_bounds_reported_but_outside_window_is_normal(tmp_path):
    recurring = _write(
        tmp_path, "recurring.csv", RECURRING_HEADER
        + "Reversed,USD,10,monthly:8,2026-09-01,2026-08-01,coupon,0\n"
        + "Expired,USD,10,monthly:8,,2026-07-01,coupon,0\n"
        + "Future,USD,10,monthly:8,2030-01-01,,coupon,0\n"
        + "Good,USD,10,once:2026-08-08,,,coupon,0\n",
    )
    proj, exc = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)
    assert exc == ["recurring row 2: start_date is after end_date"]
    assert [e.label for e in proj.events] == ["Good"]


def test_recurring_wrong_header_reported(tmp_path):
    recurring = _write(tmp_path, "recurring.csv", "a,b,c\n1,2,3\n")
    proj, exc = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY
    )
    assert proj.events == []
    assert len(exc) == 1 and "unexpected header" in exc[0]


def test_events_sorted_by_date(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Later,USD,1.00,once:2026-09-01,,,other,0\n"
        + "Sooner,USD,1.00,once:2026-08-10,,,other,0\n",
    )
    proj, _ = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)
    assert [e.label for e in proj.events] == ["Sooner", "Later"]


# ---- next inflow (reported even when it falls outside the horizon) ----


def test_next_inflow_found_beyond_the_horizon(tmp_path):
    """The real portfolio's case: horizon is all outflow, coupon is months out."""
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Loan interest,CHF,-800.00,monthly:1,,,interest,1\n"
        + "Bond coupon,USD,5000.00,once:2026-11-15,,,coupon,0\n",
    )
    proj, _ = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY, horizon_days=60
    )
    assert all(e.amount < 0 for e in proj.events)  # nothing comes in inside 60d
    assert proj.next_inflow is not None
    assert proj.next_inflow.date == date(2026, 11, 15)
    assert proj.next_inflow.amount == Decimal("5000.00")
    assert proj.next_inflow_days == (date(2026, 11, 15) - TODAY).days


def test_next_inflow_ignores_outflows_and_picks_the_earliest(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER
        + "Big later inflow,USD,9000.00,once:2026-10-01,,,coupon,0\n"
        + "Outflow,USD,-100.00,once:2026-08-20,,,interest,0\n"
        + "Small sooner inflow,USD,10.00,once:2026-09-01,,,coupon,0\n",
    )
    proj, _ = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)
    assert proj.next_inflow is not None
    assert proj.next_inflow.label == "Small sooner inflow"


def test_next_inflow_none_when_nothing_ever_comes_in(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER + "Loan interest,CHF,-800.00,monthly:1,,,interest,1\n",
    )
    proj, _ = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)
    assert proj.next_inflow is None
    assert proj.next_inflow_days is None


def test_lookahead_does_not_leak_into_horizon_events_or_net(tmp_path):
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER + "Far coupon,USD,5000.00,once:2026-11-15,,,coupon,0\n",
    )
    proj, _ = project_cashflows(
        tmp_path / "nope.csv", recurring, TODAY, horizon_days=60
    )
    assert proj.events == []
    assert proj.net == {}
    assert proj.next_inflow is not None  # still reported


def test_next_inflow_catches_an_annual_payer_just_missed(tmp_path):
    """Annual payout the day before today's anniversary — needs >365d lookahead."""
    recurring = _write(
        tmp_path,
        "recurring.csv",
        RECURRING_HEADER + "Insurance,USD,83481.00,yearly:8/1,,,insurance,1\n",
    )
    proj, _ = project_cashflows(tmp_path / "nope.csv", recurring, TODAY)
    assert proj.next_inflow is not None
    assert proj.next_inflow.date == date(2027, 8, 1)
