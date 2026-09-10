"""Tests for scoring.py: deterministic initial fit score."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from careeros import db, scoring


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")


def _insert_company(slug: str, tier: int | None, notes: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name, tier, notes) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET tier = excluded.tier, notes = excluded.notes",
            (slug, slug.title(), tier, notes),
        )
        conn.commit()


def _insert_job(job_id: str, company_slug: str, description: str, location: str, remote_type: str, comp: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, company_slug, title, description, location, remote_type, comp, fetched_at, status) "
            "VALUES (?, ?, 'Solutions Consultant', ?, ?, ?, ?, ?, 'new')",
            (job_id, company_slug, description, location, remote_type, comp, now),
        )
        conn.commit()


def _insert_bank_record(name: str, tech_tools: list[str], honesty_tag: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO bank_records (notion_page_id, name, tech_tools_json, honesty_tag) VALUES (?, ?, ?, ?)",
            (name, name, json.dumps(tech_tools), honesty_tag),
        )
        conn.commit()


def test_compute_initial_score_strong_match_tier1_disclosed_open_remote_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()
    _insert_company("acme", tier=1, notes="Warm contact via a former colleague.")
    _insert_bank_record("SSO", ["SSO", "SAML"], "Strong")
    _insert_job(
        "greenhouse::1", "acme",
        description="We need SSO and SAML experience.",
        location="Remote, Canada", remote_type="remote", comp="$150K-$180K CAD base",
    )

    score, breakdown = scoring.compute_initial_score("greenhouse::1")

    assert breakdown["bank_match"]["points"] == 6  # 1 Strong match * 6
    assert breakdown["company_tier"]["points"] == 20
    assert breakdown["comp_transparency"]["points"] == 15
    assert breakdown["geo"]["points"] == 15
    assert breakdown["warm_contact"]["points"] == 10
    assert score == 6 + 20 + 15 + 15 + 10


def test_compute_initial_score_no_match_untiered_undisclosed_baseline_geo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()
    _insert_company("manualco", tier=None, notes="Added via careeros jobs add.")
    _insert_job(
        "manual::1", "manualco",
        description="Nothing matches here.",
        location="Remote, Turkey", remote_type="remote", comp=None,
    )

    score, breakdown = scoring.compute_initial_score("manual::1")

    assert breakdown["bank_match"]["points"] == 0
    assert breakdown["company_tier"]["points"] == scoring.TIER_DEFAULT
    assert breakdown["comp_transparency"]["points"] == scoring.COMP_UNDISCLOSED_POINTS
    assert breakdown["geo"]["points"] == scoring.GEO_BASELINE_POINTS
    assert breakdown["warm_contact"]["points"] == 0


def test_bank_match_strong_only_caps_at_strong_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """10 Strong matches at 6 pts each would be 60, but Strong-only is capped
    at STRONG_MATCH_CAP (28) -- reaching the full BANK_MATCH_MAX (40)
    requires some Working matches too, by design."""
    _isolate(tmp_path, monkeypatch)
    db.init_db()
    _insert_company("acme", tier=2)
    for i in range(10):
        _insert_bank_record(f"Skill {i}", [f"tool{i}"], "Strong")
    description = " ".join(f"tool{i}" for i in range(10))
    _insert_job("greenhouse::2", "acme", description=description, location="", remote_type="unknown", comp=None)

    _, breakdown = scoring.compute_initial_score("greenhouse::2")

    assert breakdown["bank_match"]["points"] == scoring.STRONG_MATCH_CAP
    assert scoring.STRONG_MATCH_CAP < scoring.BANK_MATCH_MAX
