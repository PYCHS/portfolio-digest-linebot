# Portfolio Digest LINE Bot

[![CI](https://github.com/PYCHS/portfolio-digest-linebot/actions/workflows/ci.yml/badge.svg)](https://github.com/PYCHS/portfolio-digest-linebot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)

A production-oriented Python CLI that aggregates portfolio positions, cashflows, FX rates, and market news into a daily operations digest, then delivers it to a LINE group. It supports safe local previews, failure isolation across data sources, deduplication, automated testing, and scheduled delivery.

> **Public repo notice.** This repository is public. Real credentials and real portfolio data must never be committed. All sensitive files live under `private/` and are gitignored. Only `*.example.*` samples are committed.

## Status

**M0–M10 complete.** The end-to-end digest pipeline, LINE delivery, automated tests, scheduling, **M9 — Projected Cashflow** (coupon/interest calendar from `positions.csv` + `recurring.csv`), **M10 — LLM news-impact analysis** (Traditional-Chinese per-headline impact via the Anthropic API, graceful fallback without a key), and **M11 — daily morning greeting** (LLM-generated 早安 + encouragement + joke, offline rotation fallback) are implemented.

## Features

- Aggregates portfolio positions, actual cashflows, FX rates, and issuer news
- Projects upcoming cashflows (bond coupons, FCN coupons, loan/repo interest) onto a dated calendar with per-currency nets
- Analyzes each headline with an LLM from the portfolio's point of view (bondholder / FCN-holder), rendered in Traditional Chinese; falls back to plain headlines when no API key is set
- Opens every digest with a warm Traditional-Chinese morning greeting, encouragement, and a daily joke (LLM-generated; deterministic offline rotation without a key)
- Detects alert keywords and prevents duplicate news notifications
- Isolates collector failures so one unavailable source does not stop the digest
- Provides a safe `--dry-run` mode before sending anything to LINE
- Supports scheduled delivery through GitHub Actions, cron, or Windows Task Scheduler
- Protects credentials and portfolio data through environment variables, GitHub secrets, and gitignored private files
- Includes a comprehensive automated test suite with all external network calls mocked

## Layout

```
src/                       # application package and data collectors
tests/                     # pytest suite
config/
  watchlist.example.yaml   # safe sample (committed)
data/
  ledger.example.csv       # safe sample (committed)
  positions.example.csv    # safe sample (committed)
private/                   # gitignored — real data goes here
template.txt               # message template (single source of truth)
.env.example               # env var template (committed)
.env                       # gitignored
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[dev]
cp .env.example .env               # then fill in values
mkdir -p private
cp config/watchlist.example.yaml   private/watchlist.yaml
cp data/positions.example.csv      private/positions.csv
# Optional — only if you want actual cashflows tracked. Skip to render zeros.
cp data/ledger.example.csv         private/ledger.csv
```

### Data files

| File | Meaning | Required? |
|---|---|---|
| `private/watchlist.yaml` | News-source watchlist | yes |
| `private/positions.csv` | Holdings + coupon schedule (used by the Snapshot section) | yes |
| `private/ledger.csv` | **Actual** cash movements (deposits, coupons received, fees) | **optional** — missing file renders the Cashflow section as zeros |
| `private/recurring.csv` | Scheduled cashflows the positions schema can't express: FCN monthly coupons, loan/repo interest, insurance payouts (see `data/recurring.example.csv`) | **optional** — projection then uses bond coupons only |
| `private/llm_context.txt` | One-paragraph portfolio description injected into the LLM prompt | **optional** — a generic default is used |

`ledger.csv` and `positions.csv` are deliberately distinct: ledger captures actuals, positions captures holdings and the *expected* coupon schedule. A future milestone (M9) will add a separate Projected Cashflow section computed from the positions schedule — those projections will not be merged into the ledger and will be clearly labeled as expected.

## Environment variables

| Variable | Purpose |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | Long-lived channel access token (Messaging API) |
| `LINE_GROUP_ID` | Target LINE group ID |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`) |
| `TIMEZONE` | Always `Asia/Taipei` for this bot |
| `WATCHLIST_PATH` / `LEDGER_PATH` / `POSITIONS_PATH` / `NEWS_SEEN_PATH` | Override default file locations |
| `RECURRING_PATH` | Recurring-cashflow CSV (default `private/recurring.csv`) |
| `PROJECTION_DAYS` | Projected-cashflow horizon in days (default `60`) |
| `ANTHROPIC_API_KEY` | Enables M10 LLM news analysis; unset = plain headlines |
| `LLM_MODEL` | Anthropic model id (default `claude-haiku-4-5`) |
| `LLM_CONTEXT_PATH` | Portfolio-context text file (default `private/llm_context.txt`) |

## Obtaining LINE credentials

1. **Create a Messaging API channel.** Go to <https://developers.line.biz/console/>, create a Provider, then add a Messaging API channel under it.
2. **Channel access token (long-lived).** In the channel → *Messaging API* tab → *Channel access token (long-lived)* → **Issue**. Copy into `LINE_CHANNEL_ACCESS_TOKEN`.
3. **Group ID.** Group IDs are not in the console; you must capture one from a webhook event:
   - Add the bot account as a friend, then invite it to the target LINE group.
   - Set a temporary webhook URL (e.g. an ngrok tunnel pointing to a small logging endpoint) in the channel's Messaging API settings, and enable *Use webhook*.
   - Have a member send any message in the group. Your endpoint receives a JSON event whose `events[0].source.groupId` is the value you want.
   - Paste it into `LINE_GROUP_ID` and disable the temporary webhook.

## Running locally

```bash
# Default — renders the digest to stdout, never touches LINE
python -m src.main
python -m src.main --dry-run    # explicit alias for the default

# Real run — renders AND pushes to the configured LINE group
python -m src.main --push
```

`--push` requires `LINE_CHANNEL_ACCESS_TOKEN` and `LINE_GROUP_ID` in the environment. If either is missing the CLI exits with rc=2 before any network call. A failed push (e.g. 401, 5xx) exits with rc=3 and the LINE error message is printed to stderr.

`--push` and `--dry-run` are mutually exclusive — argparse enforces it.

## Tests

```bash
pytest
```

Network calls are mocked (`requests-mock`), so the suite never contacts live RSS, FX, or LINE services. The CI workflow runs the full suite on every push and pull request.

## Scheduling

The CLI is a single-shot job; schedule it once per day at the desired Asia/Taipei wall-clock time. Three options are wired up.

### GitHub Actions

[`.github/workflows/digest.yml`](.github/workflows/digest.yml) runs daily at **23:17 UTC** (~07:17 Asia/Taipei target, typically firing close to 08:00 Taipei) and can be triggered manually from the *Actions* tab. The off-the-hour minute is deliberate: GitHub Actions queues are heaviest at `:00 / :15 / :30 / :45 UTC`, and scheduling at `:17` reduces typical scheduler drift from ~60–90 min to ~10–30 min. To change the time, edit the `cron:` line — the value is in UTC.

Configure repo secrets under *Settings → Secrets and variables → Actions*:

| Secret | Purpose |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API token |
| `LINE_GROUP_ID` | Target LINE group ID |
| `ANTHROPIC_API_KEY` | (optional) enables LLM news analysis |
| `RECURRING_CSV_B64` | (optional) base64 of `private/recurring.csv` |
| `LLM_CONTEXT_TXT_B64` | (optional) base64 of `private/llm_context.txt` |
| `WATCHLIST_YAML_B64` | base64 of `private/watchlist.yaml` |
| `POSITIONS_CSV_B64` | base64 of `private/positions.csv` |
| `LEDGER_CSV_B64` | base64 of `private/ledger.csv` — **optional**; omit to render Cashflow as zeros |

Encode each file once and paste the result as a secret:

```bash
# Linux
base64 -w0 private/ledger.csv

# macOS
base64 < private/ledger.csv | tr -d '\n'
```

The decode step writes them into `private/` at the start of each run. If a data secret is unset, the workflow falls back to the committed `*.example.*` file — useful for the manual `dry-run` mode but not what you want for a real push.

#### Refreshing data secrets

When `private/watchlist.yaml`, `private/ledger.csv`, or `private/positions.csv` changes, re-encode the changed file and update the matching `*_B64` secret (*Settings → Secrets and variables → Actions → click the secret → Update*). The other two stay as-is. With the `gh` CLI authenticated against this repo, you can update one in place:

```bash
gh secret set LEDGER_CSV_B64 --body "$(base64 < private/ledger.csv | tr -d '\n')"
```

The LINE token and group ID don't need refreshing unless you rotate the channel token or move to a different group.

Scheduled runs always invoke `--push`; manual runs default to `--dry-run` and offer a `push` option in the dispatch input.

### Unix cron

Use the wrapper script — it resolves paths from its own location, sources `.env`, and prefers `.venv/bin/python` if present (otherwise `python3`):

```cron
# m h dom mon dow command
0 8 * * * /path/to/portfolio-digest-linebot/scripts/run_digest.sh --push >> /path/to/portfolio-digest-linebot/private/digest.log 2>&1
```

cron uses the host's local time, so set the host TZ to `Asia/Taipei` (or pick the equivalent hour in whatever zone the host runs in).

### Windows Task Scheduler

The PowerShell wrapper at [`scripts/run_digest.ps1`](scripts/run_digest.ps1) mirrors the bash version. Register it as a daily task:

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\run_digest.ps1`" -Push" `
    -WorkingDirectory $PWD
$trigger = New-ScheduledTaskTrigger -Daily -At 8am
Register-ScheduledTask -TaskName "PortfolioDigest" -Action $action -Trigger $trigger
```

The script prefers `.venv\Scripts\python.exe` if present, otherwise `python` on PATH. Set the host time zone to `Asia/Taipei` (or adjust `-At`).

## Milestone plan

| # | Scope |
|---|---|
| M0 | Scaffold, gitignore, env template, CI running empty pytest ✅ |
| M1 | Models + formatter + snapshot test against `template.txt` ✅ |
| M2 | Ledger source (Today / MTD / Bal per currency) ✅ |
| M3 | FX source (Frankfurter, USD/CHF + reciprocal + DoD%) ✅ |
| M4 | Positions source (Total Cost, Est. Annual Coupon, Next Coupon 7d) ✅ |
| M5 | News source + dedup + alert keywords ✅ |
| M6 | Orchestrator, partial-failure isolation, `--dry-run`, structured logging ✅ |
| M7 | LINE client + secret-hygiene test ✅ |
| M8 | Scheduling (GitHub Actions / cron / Task Scheduler) ✅ |
| M9 | Projected Cashflow section computed from positions schedule (separate from ledger actuals) — planned |
