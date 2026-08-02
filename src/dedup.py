from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(title: str) -> str:
    s = title.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


@dataclass
class SeenEntry:
    title_norm: str
    first_seen: str  # ISO 8601 with tz


def load_seen(path: Path) -> list[SeenEntry]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("entries", [])
        out: list[SeenEntry] = []
        for e in raw:
            if isinstance(e, dict) and "title_norm" in e and "first_seen" in e:
                out.append(SeenEntry(title_norm=e["title_norm"], first_seen=e["first_seen"]))
        return out
    except (json.JSONDecodeError, OSError):
        return []


def save_seen(path: Path, entries: list[SeenEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [asdict(e) for e in entries]}
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def prune_old(entries: list[SeenEntry], now: datetime, days: int) -> list[SeenEntry]:
    cutoff = now - timedelta(days=days)
    out: list[SeenEntry] = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e.first_seen)
        except ValueError:
            continue
        if ts >= cutoff:
            out.append(e)
    return out


def is_duplicate(title: str, entries: list[SeenEntry], threshold: float) -> bool:
    norm = normalize(title)
    if not norm:
        return False
    cutoff_pct = threshold * 100
    for e in entries:
        if e.title_norm == norm:
            return True
        if fuzz.token_set_ratio(norm, e.title_norm) >= cutoff_pct:
            return True
    return False
