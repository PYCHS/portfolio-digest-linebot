# Portfolio Digest LINE Bot

A single-shot CLI that builds a daily Portfolio Ops Digest and pushes it to a LINE group.

> **Public repo notice.** This repository is public. Real credentials and real portfolio data must never be committed. All sensitive files live under `private/` and are gitignored. Only `*.example.*` samples are committed.

## Status

Milestone **M0 — Scaffold** (current). Collectors, formatter, and LINE client land in subsequent milestones (see [Milestone plan](#milestone-plan)).

## Layout

```
src/                       # application package (filled in M1+)
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
cp config/watchlist.example.yaml private/watchlist.yaml
cp data/ledger.example.csv         private/ledger.csv
cp data/positions.example.csv      private/positions.csv
```

## Environment variables

| Variable | Purpose |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | Long-lived channel access token (Messaging API) |
| `LINE_CHANNEL_SECRET` | Channel secret |
| `LINE_GROUP_ID` | Target LINE group ID |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`) |
| `TIMEZONE` | Always `Asia/Taipei` for this bot |
| `WATCHLIST_PATH` / `LEDGER_PATH` / `POSITIONS_PATH` / `NEWS_SEEN_PATH` | Override default file locations |

## Obtaining LINE credentials

1. **Create a Messaging API channel.** Go to <https://developers.line.biz/console/>, create a Provider, then add a Messaging API channel under it.
2. **Channel access token (long-lived).** In the channel → *Messaging API* tab → *Channel access token (long-lived)* → **Issue**. Copy into `LINE_CHANNEL_ACCESS_TOKEN`.
3. **Channel secret.** *Basic settings* tab → *Channel secret*. Copy into `LINE_CHANNEL_SECRET`.
4. **Group ID.** Group IDs are not in the console; you must capture one from a webhook event:
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

Network calls in tests are mocked (`requests-mock`). No live RSS / FX / LINE traffic.

## Scheduling

Filled in during **M8**. Targets: GitHub Actions daily cron, Windows Task Scheduler, and Unix cron — all running `python -m src.main` once per day at the desired Asia/Taipei wall-clock time.

## Milestone plan

| # | Scope |
|---|---|
| M0 | Scaffold, gitignore, env template, CI running empty pytest |
| M1 | Models + formatter + snapshot test against `template.txt` |
| M2 | Ledger source (Today / MTD / Bal per currency) |
| M3 | FX source (Frankfurter, USD/CHF + reciprocal + DoD%) |
| M4 | Positions source (Total Cost, Est. Annual Coupon, Next Coupon 7d) |
| M5 | News source + dedup + alert keywords |
| M6 | Orchestrator, partial-failure isolation, `--dry-run`, structured logging |
| M7 | LINE client + secret-hygiene test |
| M8 | Scheduling (GitHub Actions / cron / Task Scheduler) |
