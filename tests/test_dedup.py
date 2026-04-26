from datetime import datetime, timedelta, timezone

from src.dedup import (
    SeenEntry,
    is_duplicate,
    load_seen,
    normalize,
    prune_old,
    save_seen,
)

UTC = timezone.utc
NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


def test_normalize_lowercases_strips_punctuation_and_collapses_whitespace():
    assert normalize("ACME: Q1 results — solid!") == "acme q1 results solid"
    assert normalize("  Multiple   spaces\tand\nnewlines  ") == "multiple spaces and newlines"


def test_is_duplicate_exact_match():
    seen = [SeenEntry(title_norm=normalize("ACME Q1 results"), first_seen=NOW.isoformat())]
    assert is_duplicate("ACME Q1 results", seen, threshold=0.85) is True


def test_is_duplicate_near_match_above_threshold():
    seen = [SeenEntry(title_norm=normalize("ACME Q1 results in line with guidance"), first_seen=NOW.isoformat())]
    # Reordered tokens — token_set_ratio should rate them high
    assert is_duplicate("In line with guidance: ACME Q1 results", seen, threshold=0.85) is True


def test_is_duplicate_distinct_titles_below_threshold():
    seen = [SeenEntry(title_norm=normalize("ACME Q1 results"), first_seen=NOW.isoformat())]
    assert is_duplicate("Beta Capital appoints new CFO", seen, threshold=0.85) is False


def test_is_duplicate_against_empty_seen_list():
    assert is_duplicate("Anything goes", [], threshold=0.85) is False


def test_prune_old_drops_entries_beyond_window():
    fresh = SeenEntry(title_norm="fresh", first_seen=(NOW - timedelta(days=1)).isoformat())
    stale = SeenEntry(title_norm="stale", first_seen=(NOW - timedelta(days=4)).isoformat())
    out = prune_old([fresh, stale], now=NOW, days=3)
    assert [e.title_norm for e in out] == ["fresh"]


def test_prune_old_skips_corrupt_timestamps():
    good = SeenEntry(title_norm="good", first_seen=NOW.isoformat())
    bad = SeenEntry(title_norm="bad", first_seen="not-a-date")
    out = prune_old([good, bad], now=NOW, days=3)
    assert [e.title_norm for e in out] == ["good"]


def test_save_and_load_round_trip(tmp_path):
    entries = [
        SeenEntry(title_norm="acme q1 results", first_seen=NOW.isoformat()),
        SeenEntry(title_norm="beta cfo appointed", first_seen=NOW.isoformat()),
    ]
    p = tmp_path / "seen.json"
    save_seen(p, entries)
    reloaded = load_seen(p)
    assert reloaded == entries


def test_load_seen_returns_empty_for_missing_file(tmp_path):
    assert load_seen(tmp_path / "does_not_exist.json") == []


def test_load_seen_recovers_from_corrupt_json(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_seen(p) == []


def test_load_seen_skips_malformed_entries(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text(
        '{"entries": [{"title_norm": "ok", "first_seen": "2026-04-25T12:00:00+00:00"},'
        '{"junk": true}]}',
        encoding="utf-8",
    )
    out = load_seen(p)
    assert len(out) == 1
    assert out[0].title_norm == "ok"
