from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.main as main_module
from src.main import main

TPE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=TPE)


def _write_files(tmp_path: Path) -> dict[str, Path]:
    ledger = tmp_path / "ledger.csv"
    ledger.write_text(
        "date,amount,currency,category,description\n"
        "2026-04-01,1000.00,USD,deposit,April funding\n"
        "2026-04-25,55.00,USD,coupon,Coupon today\n",
        encoding="utf-8",
    )
    positions = tmp_path / "positions.csv"
    positions.write_text(
        "instrument_type,issuer_or_name,isin_or_code,trade_date,quantity,"
        "coupon_rate_pct,maturity,buy_price,cost,annual_interest,"
        "semiannual_interest,yield_pct_table,current_yield_pct,coupon_dates\n"
        "bond,Issuer Alpha,XS0000000001,20250101,10000,5.00,2030,"
        "98.50,985000.00,50000.00,25000.00,5.20,5.0761,11/01;5/01\n",
        encoding="utf-8",
    )
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "issuers:\n"
        "  - id: ACME\n"
        "    name: ACME Holdings AG\n"
        "    rss:\n"
        "      - https://example.com/acme.rss\n"
        "    enabled: true\n"
        "settings:\n"
        "  lookback_hours: 24\n"
        "  max_items_per_issuer: 1\n"
        "  dedup_lookback_days: 3\n"
        "  similarity_threshold: 0.85\n"
        "  alert_keywords: []\n",
        encoding="utf-8",
    )
    seen = tmp_path / "seen.json"
    return {"ledger": ledger, "positions": positions, "watchlist": watchlist, "seen": seen}


def _setup_env(monkeypatch, paths: dict[str, Path]) -> None:
    monkeypatch.setenv("LEDGER_PATH", str(paths["ledger"]))
    monkeypatch.setenv("POSITIONS_PATH", str(paths["positions"]))
    monkeypatch.setenv("WATCHLIST_PATH", str(paths["watchlist"]))
    monkeypatch.setenv("NEWS_SEEN_PATH", str(paths["seen"]))
    monkeypatch.setenv("TIMEZONE", "Asia/Taipei")
    monkeypatch.setattr(main_module, "_now_in_tz", lambda _tz: NOW)


def _setup_http(requests_mock) -> None:
    requests_mock.get(
        "https://api.frankfurter.app/latest",
        json={"date": "2026-04-25", "rates": {"CHF": 0.9123}},
    )
    requests_mock.get(
        "https://api.frankfurter.app/2026-04-24",
        json={"date": "2026-04-24", "rates": {"CHF": 0.9134}},
    )
    requests_mock.get(
        "https://example.com/acme.rss",
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<rss version=\"2.0\"><channel>"
            "<title>ACME Press</title>"
            "<item>"
            "<title>ACME Q1 results in line with guidance</title>"
            "<link>https://acme.com/q1</link>"
            "<pubDate>Sat, 25 Apr 2026 10:00:00 +0000</pubDate>"
            "</item>"
            "</channel></rss>"
        ),
    )


def test_dry_run_renders_message_with_data_from_all_sources(
    tmp_path, monkeypatch, requests_mock, capsys
):
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)

    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out

    assert out.startswith(
        "【Daily Investment Digest (每日投資摘要)】2026-04-25 (Asia/Taipei)"
    )
    assert "✅ Status (狀態): All clear (一切正常)" in out
    # News from RSS
    assert "ACME: ACME Q1 results in line with guidance" in out
    # FX (rates rounded to 4dp; DoD = (0.9123 - 0.9134) / 0.9134 * 100 = -0.12)
    assert "USD/CHF: 0.9123 (Δ -0.12% DoD)" in out
    assert "CHF/USD: 1.0961" in out
    # Snapshot
    assert "Total Cost (總成本): 985,000.00 USD" in out
    assert "Est. Annual Coupon (預估年息): 50,000.00 USD" in out
    # Cashflow: today=55.00 USD, MTD=1055.00 USD, both 0 CHF
    assert "Today (今日): +55.00 USD | +0.00 CHF" in out
    assert "MTD (本月累計): +1,055.00 USD | +0.00 CHF" in out
    assert "Bal (餘額): USD 1,055.00 | CHF 0.00" in out


def test_default_run_renders_to_stdout_without_pushing(
    tmp_path, monkeypatch, requests_mock, capsys
):
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    # Make absolutely sure no env-driven push is attempted
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_GROUP_ID", raising=False)

    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Daily Investment Digest" in out
    # No POST to LINE was made
    assert not any("line.me" in r.url for r in requests_mock.request_history)


def test_push_flag_sends_rendered_message_to_line_group(
    tmp_path, monkeypatch, requests_mock, capsys
):
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_GROUP_ID", "C-test-group")
    requests_mock.post("https://api.line.me/v2/bot/message/push", status_code=200, json={})

    rc = main(["--push"])
    assert rc == 0

    push_reqs = [r for r in requests_mock.request_history if "line.me" in r.url]
    assert len(push_reqs) == 1
    body = push_reqs[0].json()
    assert body["to"] == "C-test-group"
    assert body["messages"][0]["type"] == "text"
    assert "Daily Investment Digest" in body["messages"][0]["text"]
    assert push_reqs[0].headers["Authorization"] == "Bearer test-token"

    out = capsys.readouterr().out
    assert "(pushed to LINE)" in out


def test_push_without_required_env_returns_rc2(
    tmp_path, monkeypatch, requests_mock, capsys
):
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_GROUP_ID", raising=False)

    rc = main(["--push"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "LINE_CHANNEL_ACCESS_TOKEN" in err
    # No HTTP POST attempted
    assert not any("line.me" in r.url for r in requests_mock.request_history)


def test_push_failure_returns_rc3(
    tmp_path, monkeypatch, requests_mock, capsys
):
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_GROUP_ID", "C-test-group")
    requests_mock.post(
        "https://api.line.me/v2/bot/message/push",
        status_code=401,
        json={"message": "Authentication failed"},
    )

    rc = main(["--push"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "LINE push failed" in err
    assert "401" in err


def test_push_and_dry_run_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        main(["--push", "--dry-run"])
    err = capsys.readouterr().err
    assert "not allowed with" in err.lower()


def test_invalid_timezone_returns_rc2_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("TIMEZONE", "Not/A_Real_Timezone")

    rc = main(["--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid TIMEZONE" in err
    assert "Not/A_Real_Timezone" in err


def test_invalid_log_level_returns_rc2_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    rc = main(["--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid LOG_LEVEL" in err
    assert "VERBOSE" in err


def test_dry_run_does_not_write_news_seen_file(
    tmp_path, monkeypatch, requests_mock, capsys
):
    """Dry-run is a preview — it must not consume dedup state. Otherwise
    running --dry-run before --push surfaces different items in each."""
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    assert not paths["seen"].exists()

    rc = main(["--dry-run"])
    assert rc == 0
    assert not paths["seen"].exists(), "--dry-run wrote seen.json"


def test_push_writes_news_seen_file(
    tmp_path, monkeypatch, requests_mock, capsys
):
    """--push commits dedup state so tomorrow's run doesn't re-send the same
    headlines."""
    paths = _write_files(tmp_path)
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_GROUP_ID", "C-test-group")
    requests_mock.post("https://api.line.me/v2/bot/message/push", status_code=200, json={})

    rc = main(["--push"])
    assert rc == 0
    assert paths["seen"].exists(), "--push did not write seen.json"


def test_dry_run_without_ledger_renders_zero_cashflow_without_notes_noise(
    tmp_path, monkeypatch, requests_mock, capsys
):
    """Ledger is optional — running with no ledger.csv should produce a clean
    digest: Cashflow shows zeros, and no Data-gap / exception line mentions
    the ledger."""
    paths = _write_files(tmp_path)
    paths["ledger"] = tmp_path / "no_ledger.csv"  # never created
    _setup_env(monkeypatch, paths)
    _setup_http(requests_mock)

    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out

    assert "Today (今日): +0.00 USD | +0.00 CHF" in out
    assert "MTD (本月累計): +0.00 USD | +0.00 CHF" in out
    assert "Bal (餘額): USD 0.00 | CHF 0.00" in out
    assert "ledger: file not found" not in out
    assert "Data gaps: Ledger" not in out
