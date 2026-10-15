"""Tests for scoring.py: live fit score (Bank match, comp, remote, warm contact)."""

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
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    db.init_db()


def _insert_company(slug: str, notes: str = "") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name, notes) VALUES (?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET notes = excluded.notes",
            (slug, slug.title(), notes),
        )
        conn.commit()


def _insert_job(
    job_id: str, company_slug: str, description: str = "", remote_type: str = "unknown",
    comp: str | None = None, application_stage: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, company_slug, title, description, remote_type, comp, application_stage, fetched_at, status) "
            "VALUES (?, ?, 'Solutions Consultant', ?, ?, ?, ?, ?, 'new')",
            (job_id, company_slug, description, remote_type, comp, application_stage, now),
        )
        conn.commit()


def _insert_bank_record(name: str, tech_tools: list[str], honesty_tag: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO bank_records (notion_page_id, name, tech_tools_json, honesty_tag) VALUES (?, ?, ?, ?)",
            (name, name, json.dumps(tech_tools), honesty_tag),
        )
        conn.commit()


# --- comp parsing -------------------------------------------------------

@pytest.mark.parametrize(
    "comp,expected_k",
    [
        # Current format: landed leads unlabeled, "- Posted:" trails.
        ("$118K base - Posted: $94-151K base ($126-189K OTE)", 118),
        ("$110-120K base ($137-150K OTE) - Posted: $89-120K base ($111-150K OTE)", 120),
        ("$130-140K base - Posted: $118-158K base (OTE unconfirmed)", 140),
        ("Posted: Base unconfirmed ($160-220K OTE)", None),
        ("N/A", None),
        (None, None),
        ("Posted: $162-166K base (186-190K OTE)", 166),
        # Older labeled format still parses.
        ("Posted: $94-151K base ($126-189K OTE) / Landed: $118K base", 118),
        ("Post: $89-120K base ($111-150K OTE) / Landed: $110-120K base ($137-150K OTE)", 120),
    ],
)
def test_highest_base_figure_parsing(comp: str | None, expected_k: int | None) -> None:
    assert scoring._highest_base_figure_k(comp) == expected_k


# --- comp component -------------------------------------------------------

def test_comp_component_well_above_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    pts, _ = scoring._comp_component("Landed: $150K base")
    assert pts == scoring.COMP_WELL_ABOVE_FLOOR


def test_comp_component_at_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    pts, _ = scoring._comp_component("Landed: $121K base")
    assert pts == scoring.COMP_AT_FLOOR


def test_comp_component_below_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    pts, _ = scoring._comp_component("Landed: $90K base")
    assert pts == scoring.COMP_WELL_BELOW_FLOOR


def test_comp_component_unknown_is_neutral(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    pts, _ = scoring._comp_component("N/A")
    assert pts == scoring.COMP_UNKNOWN


def test_comp_component_is_proportional_not_a_cliff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real case (2026-09-17): $118K vs a $120K floor is a ~2% gap and
    # shouldn't cost anywhere near half of COMP_MAX, the way the old fixed
    # bands did (COMP_AT_FLOOR=24 vs COMP_BELOW_FLOOR_CLOSE=13, an 11pt
    # cliff for 2%).
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    at_floor, _ = scoring._comp_component("Landed: $120K base")
    just_under, _ = scoring._comp_component("Landed: $118K base")
    assert 0 < at_floor - just_under <= 3


def test_comp_component_is_monotonic_with_figure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "COMP_FLOOR", 120000)
    figures = ["$95K", "$105K", "$115K", "$120K", "$125K", "$138K", "$150K"]
    points = [scoring._comp_component(f"Landed: {f} base")[0] for f in figures]
    assert points == sorted(points)


# --- warm contact component --------------------------------------------------
# Stage/application progress was removed from scoring entirely (2026-09-17,
# see scoring.py's module docstring) -- it's a momentum signal, not a fit
# signal. Only the warm-contact bonus remains from the old landing component.

def test_warm_contact_component_bonus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _insert_company("warmco", notes="Warm contact via a former colleague.")
    _insert_company("coldco", notes="")
    warm, warm_label = scoring._warm_contact_component("warmco")
    cold, cold_label = scoring._warm_contact_component("coldco")
    assert warm == scoring.WARM_CONTACT_BONUS
    assert cold == 0
    assert "warm contact" in warm_label
    assert "no warm contact" in cold_label


# --- full integration -------------------------------------------------------

def test_compute_initial_score_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _insert_company("acme", notes="Warm contact via a former colleague.")
    _insert_bank_record("SSO", ["SSO", "SAML"], "Strong")
    _insert_job(
        "greenhouse::1", "acme",
        description="We need SSO and SAML experience.",
        remote_type="remote", comp="Landed: $150K base",
        application_stage="3. Second Round Interview",
    )

    score, breakdown = scoring.compute_initial_score("greenhouse::1")

    assert breakdown["bank_match"]["points"] == scoring.STRONG_MATCH_POINTS  # 1 Strong match
    assert breakdown["comp"]["points"] == scoring.COMP_WELL_ABOVE_FLOOR
    assert breakdown["remote"]["points"] == scoring.REMOTE_POINTS["remote"]
    assert breakdown["warm_contact"]["points"] == scoring.WARM_CONTACT_BONUS  # stage doesn't factor in
    assert score == (
        breakdown["bank_match"]["points"] + breakdown["comp"]["points"]
        + breakdown["remote"]["points"] + breakdown["warm_contact"]["points"]
    )


def test_describe_score_is_short_and_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _insert_company("acme")
    _insert_job("greenhouse::2", "acme", remote_type="hybrid", comp="N/A", application_stage=None)

    _, breakdown = scoring.compute_initial_score("greenhouse::2")
    text = scoring.describe_score(breakdown)

    assert len(text) <= 200
    assert "hybrid" in text
