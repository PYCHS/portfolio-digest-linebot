"""Structural checks for the scheduling artifacts shipped in M8.

These don't simulate a cron run — they just guard against the workflow YAML
or the wrapper scripts being silently broken (renamed CLI module, missing
schedule trigger, etc.).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "digest.yml"
SH_SCRIPT = ROOT / "scripts" / "run_digest.sh"
PS_SCRIPT = ROOT / "scripts" / "run_digest.ps1"


def _load_workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on:` as the boolean True under YAML 1.1.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def test_digest_workflow_has_daily_cron_and_manual_dispatch():
    data = _load_workflow()
    triggers = data["on"]
    schedules = triggers.get("schedule") or []
    crons = [s["cron"] for s in schedules if "cron" in s]
    # 23:17 UTC = ~07:17 Asia/Taipei target; the off-the-hour minute is
    # deliberate to dodge GHA's top-of-hour scheduling queues so the run
    # actually fires close to the intended 08:00 Taipei.
    assert "17 23 * * *" in crons, f"expected off-hour TPE cron, got {crons!r}"
    assert "workflow_dispatch" in triggers, "manual trigger not enabled"


def test_digest_workflow_invokes_cli_with_push_on_schedule():
    data = _load_workflow()
    job = next(iter(data["jobs"].values()))
    steps_text = "\n".join(yaml.safe_dump(s) for s in job["steps"])
    assert "src.main" in steps_text, "workflow does not invoke `python -m src.main`"
    assert "--push" in steps_text, "workflow has no --push branch"
    assert "--dry-run" in steps_text, "workflow has no --dry-run branch"


def test_digest_workflow_passes_line_secrets_via_env():
    data = _load_workflow()
    job = next(iter(data["jobs"].values()))
    job_env = job.get("env", {})
    # Job-level env should reference the secrets so main.py sees them at runtime.
    assert "LINE_CHANNEL_ACCESS_TOKEN" in job_env
    assert "LINE_GROUP_ID" in job_env
    assert "secrets.LINE_CHANNEL_ACCESS_TOKEN" in job_env["LINE_CHANNEL_ACCESS_TOKEN"]
    assert "secrets.LINE_GROUP_ID" in job_env["LINE_GROUP_ID"]


def test_unix_wrapper_is_executable_and_invokes_cli():
    assert SH_SCRIPT.exists(), "scripts/run_digest.sh is missing"
    mode = SH_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/run_digest.sh is not executable"
    body = SH_SCRIPT.read_text(encoding="utf-8")
    assert body.startswith("#!"), "shebang missing"
    assert "src.main" in body
    # Forwards argv so callers can pass --push / --dry-run.
    assert '"$@"' in body


def test_windows_wrapper_invokes_cli_and_handles_push_flag():
    assert PS_SCRIPT.exists(), "scripts/run_digest.ps1 is missing"
    body = PS_SCRIPT.read_text(encoding="utf-8")
    assert "src.main" in body
    assert "-Push" in body
    assert ".venv" in body, "wrapper should prefer the project venv"
