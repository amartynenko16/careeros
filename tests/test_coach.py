"""Tests for coach.py: grounded gap analysis against Bank tech/tool vocabulary."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from careeros import coach, db


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")


def _insert_record(
    page_id: str,
    name: str,
    tech_tools: list[str],
    honesty_tag: str | None = None,
    local_honesty_tag: str | None = None,
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO bank_records (notion_page_id, name, tech_tools_json, honesty_tag, local_honesty_tag)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_id, name, json.dumps(tech_tools), honesty_tag, local_honesty_tag),
        )
        conn.commit()


def _insert_job(job_id: str, title: str, description: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name) VALUES ('acme', 'Acme')"
        )
        conn.execute(
            """
            INSERT INTO jobs (id, company_slug, title, description, fetched_at, status)
            VALUES (?, 'acme', ?, ?, ?, 'new')
            """,
            (job_id, title, description, now),
        )
        conn.commit()


def test_analyze_groups_by_honesty_and_excludes_never_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "API integrations", ["REST", "Postman"], honesty_tag="Strong")
    _insert_record("p2", "SQL troubleshooting", ["SQL"], honesty_tag="Working")
    _insert_record("p3", "Old internal tool", ["Salesforce"], honesty_tag="Never Claim")
    _insert_record("p4", "Untagged record", ["Zendesk"])
    _insert_job("acme::1", "Solutions Consultant", "You'll work with REST APIs, SQL, and Zendesk daily.")

    report = coach.analyze("acme::1")

    assert set(report["matches"].keys()) == {"Strong", "Working", "Untagged"}
    assert report["matches"]["Strong"][0]["record"]["name"] == "API integrations"
    assert report["matches"]["Strong"][0]["matched_terms"] == ["REST"]
    assert report["matches"]["Working"][0]["matched_terms"] == ["SQL"]
    assert report["matches"]["Untagged"][0]["matched_terms"] == ["Zendesk"]
    # Never Claim record's tool never appears, matched or unmatched.
    all_terms = str(report["matches"]) + str(report["unmatched_bank_tools"])
    assert "Salesforce" not in all_terms


def test_local_honesty_tag_overrides_notion_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record(
        "p1", "Record", ["Kubernetes"], honesty_tag="Working", local_honesty_tag="Strong"
    )
    _insert_job("acme::1", "Role", "Requires Kubernetes experience.")

    report = coach.analyze("acme::1")
    assert "Strong" in report["matches"]
    assert "Working" not in report["matches"]


def test_unmatched_bank_tools_excludes_matched_and_never_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "R1", ["Postman"], honesty_tag="Strong")
    _insert_record("p2", "R2", ["Terraform"], honesty_tag="Strong")
    _insert_record("p3", "R3", ["Never Claim Tool"], honesty_tag="Never Claim")
    _insert_job("acme::1", "Role", "You'll use Postman regularly.")

    report = coach.analyze("acme::1")
    assert report["unmatched_bank_tools"] == ["Terraform"]


def test_analyze_matches_singular_when_bank_term_is_plural(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real bug found 2026-09-10: AfterShip's JD said "REST API expertise"
    # (singular) but the Bank's stored term is "REST APIs" (plural), and a
    # strict substring check silently missed it, scoring a Strong record as
    # zero Bank match despite the text being right there.
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "REST APIs and Integration Architecture", ["REST APIs"], honesty_tag="Strong")
    _insert_job("acme::1", "Role", "ERP and REST API expertise advantageous.")

    report = coach.analyze("acme::1")
    assert "Strong" in report["matches"]
    assert report["matches"]["Strong"][0]["matched_terms"] == ["REST APIs"]


def test_analyze_matches_plural_when_bank_term_is_singular(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "Webhook handling", ["Webhook"], honesty_tag="Strong")
    _insert_job("acme::1", "Role", "You'll configure webhooks for every customer.")

    report = coach.analyze("acme::1")
    assert "Strong" in report["matches"]


def test_analyze_short_acronym_ending_in_s_not_over_stripped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "AWS" must not get stripped to the 2-char fragment "AW" (which could
    # false-positive inside unrelated words even with a word boundary).
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "Cloud storage", ["AWS"], honesty_tag="Working")
    _insert_job("acme::1", "Role", "Big data and analytics platform, no AWS experience needed.")

    report = coach.analyze("acme::1")
    # Real "AWS" mention still matches...
    assert "Working" in report["matches"]

    _insert_job("acme::2", "Role2", "We help you draw insights and see the bigger picture.")
    report2 = coach.analyze("acme::2")
    # ...but "aw"-containing words like "draw" must not false-positive.
    assert "Working" not in report2["matches"]


def test_analyze_no_partial_word_false_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Word-boundary matching: "SQL" must not match inside an unrelated
    # longer token.
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "Database work", ["SQL"], honesty_tag="Strong")
    _insert_job("acme::1", "Role", "We use NoSQLDB for everything, no relational database here.")

    report = coach.analyze("acme::1")
    assert "Strong" not in report["matches"]


def test_compare_sorts_by_strong_matches_then_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    db.init_db()

    _insert_record("p1", "R1", ["Postman"], honesty_tag="Strong")
    _insert_record("p2", "R2", ["SQL"], honesty_tag="Working")
    _insert_job("acme::1", "Weak fit", "SQL only.")
    _insert_job("acme::2", "Strong fit", "Postman and SQL both.")

    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'saved'")
        conn.commit()

    results = coach.compare(status="saved")
    assert results[0]["job"]["id"] == "acme::2"
    assert results[0]["counts"]["Strong"] == 1
    assert results[1]["job"]["id"] == "acme::1"
