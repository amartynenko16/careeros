"""Smoke tests for Phase 1 (minus sync).

These verify the basic pieces work end-to-end without requiring a network,
Notion token, or real Bank data.
"""

import json
from pathlib import Path

import pytest

from careeros import db, facts
from careeros.config import DATA_DIR


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DB and data paths at a temp directory for test isolation."""
    from careeros import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "FACTS_YAML_PATH", tmp_path / "facts.yaml")
    # Patch the same names inside modules that already imported them.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(facts, "FACTS_YAML_PATH", tmp_path / "facts.yaml")


def test_init_creates_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()
    counts = db.table_counts()
    # Every declared table is present and starts empty.
    assert set(counts) == {
        "bank_records",
        "immutable_facts",
        "companies",
        "jobs",
        "applications",
        "sync_state",
    }
    assert all(n == 0 for n in counts.values())


def test_facts_load_and_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    # Write a minimal facts.yaml.
    (tmp_path / "facts.yaml").write_text(
        "personal:\n"
        "  name: Jordan Rivera\n"
        "  email: jordan.rivera@example.com\n"
        "education:\n"
        "  - institution: Example University\n"
        "    degree: Bachelor's Degree\n"
        "    dates: '2007 to 2011'\n",
        encoding="utf-8",
    )
    n = facts.load_from_yaml()
    assert n == 5  # 2 personal keys + 3 education keys

    assert facts.get("personal.name") == "Jordan Rivera"
    assert facts.get("education[0].institution") == "Example University"
    assert facts.get("does.not.exist") is None


def test_facts_example_yaml_ships_valid() -> None:
    """The shipped data/facts.example.yaml (the actual template, since real
    facts.yaml is gitignored personal data) parses and has the expected
    top-level sections and shape -- not exact counts, since anyone copying
    this template will change them immediately."""
    import yaml

    path = DATA_DIR / "facts.example.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "personal" in data
    assert "education" in data
    assert "employers" in data
    assert "certifications" in data
    assert len(data["employers"]) >= 1
    assert len(data["certifications"]) >= 1
