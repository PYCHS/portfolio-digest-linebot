# Project overview

Snapshot as of commit `8e7abb0` (M4.1).

**6 commits, ~450 lines of source, ~450 lines of tests, 36 tests all mocked, all green.** Five working milestones plus a cleanup, and three more planned.

## What's been built

| Commit | Milestone | What it adds |
|---|---|---|
| `0ec87a2` | **M0** | Repo scaffold: `.gitignore` keeping `private/` and `.env` off GitHub, `.env.example` template, `pyproject.toml` with runtime + dev deps, README with LINE-credential walkthrough, GitHub Actions CI (`pytest` on push/PR), and committed example data files. |
| `e522167` | **M1** | The data contract: dataclasses (`NewsItem`, `FxResult`, `Cashflow`, `Coupon`, `Snapshot`, `DigestInput`) plus a [src/formatter.py](../src/formatter.py) `render()` that produces the LINE message. Two golden fixtures lock the message format byte-for-byte against [template.txt](../template.txt). |
| `43fb243` | **M2** | [src/sources/ledger.py](../src/sources/ledger.py) — reads the 5-column ledger CSV, aggregates Today/MTD/Bal per currency for USD + CHF, isolates row failures from collector failures. |
| `76b0fbe` | **M3** | [src/sources/fx.py](../src/sources/fx.py) — Frankfurter no-key API for USD/CHF, computes CHF/USD as the rounded reciprocal, and DoD% by querying the date one day prior to whatever `/latest` returned (handles weekends/holidays). |
| `335cb50` | **M4** | [src/sources/positions.py](../src/sources/positions.py) — the real 14-column positions schema, USD default with a `currency` column hook for the future, FCN/notes excluded from Total Cost and surfaced as "Cost unavailable" lines. Reshapes `Snapshot` to per-currency dicts. |
| `8e7abb0` | **M4.1** | Cleanup from the audit: fixes a bug where `semiannual_interest` parse errors were silently swallowed for rows with far-future coupons; removes the unused `DataGap` dataclass; adds a regression test plus two coverage gaps. |

## How it's architected

**One pattern repeated four times.** Every source collector has the same shape:

```python
def load_X(...) -> tuple[X | None, list[str]]:
```

- `(X, [])` — clean success
- `(X, ["row 5: bad amount …"])` — partial success, bad rows logged but data still returned
- `(None, ["X: file not found"])` — full failure, formatter renders "(unavailable)" and adds a Notes gap line

This means a flaky news feed can never bring down the whole digest: the orchestrator (M6) just runs each collector in its own try/except and passes the four results into a single `DigestInput`, which the formatter renders mechanically.

**Determinism by design.** No collector reads the clock — `today` is always passed in as a parameter. The orchestrator computes Asia/Taipei "today" once and threads it through. This is why all 36 tests run in ~50ms with no clock mocking.

**Privacy boundary.** [`private/`](../private/) is gitignored; [`data/*.example.csv`](../data/) and [`config/*.example.yaml`](../config/) hold sanitized analogs. Real data (your `private/positions.csv`, `private/ledger.csv`, `private/.news_seen.json`) never gets staged. `CLAUDE.md` is excluded via `.git/info/exclude` (local-only — keeps the filename itself off GitHub).

**Rendering contract over implementation.** M1 locked the output format to a golden file *before* any collector existed. Every subsequent milestone added a source feeding that contract, never the other way around — so each PR was independent and the message format never drifted.

**Decimal-first money math.** All amounts use `Decimal`, not float. FX rates round HALF_UP at 4dp, percentages at 2dp, money at 2dp with explicit `+/-` signs in cashflow lines.

## What's left

- **M5** — News collector: per-issuer RSS, Google News fallback, `rapidfuzz` similarity dedup persisted to `private/.news_seen.json`, alert-keyword scanning to flip the status header.
- **M6** — `src/main.py` orchestrator: TZ logic, `--dry-run` flag, structured logging, end-to-end mock test.
- **M7** — LINE Messaging API push client, secret-hygiene test that scans the repo for token-shaped strings.
- **M8** — Scheduling: GitHub Actions daily cron + Windows Task Scheduler / Unix cron snippets in README.
