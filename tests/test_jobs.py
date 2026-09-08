"""Tests for jobs.py triage operations, isolated from the real careeros.db."""

from pathlib import Path

import pytest

from careeros import db, jobs


def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _insert_job(job_id: str = "greenhouse::123") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name, ats_type) VALUES ('acme', 'Acme', 'greenhouse') "
            "ON CONFLICT(slug) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO jobs (id, company_slug, ats_type, title, remote_type, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, "acme", "greenhouse", "Solutions Consultant", "unknown", "new"),
        )
        conn.commit()


def test_set_remote_type_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    _insert_job()

    jobs.set_remote_type("greenhouse::123", "hybrid")

    assert jobs.get("greenhouse::123")["remote_type"] == "hybrid"


def test_set_remote_type_rejects_unknown_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    _insert_job()

    with pytest.raises(jobs.InvalidRemoteTypeError):
        jobs.set_remote_type("greenhouse::123", "fully-remote")

    assert jobs.get("greenhouse::123")["remote_type"] == "unknown"


def test_set_remote_type_missing_job_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_db(tmp_path, monkeypatch)

    with pytest.raises(jobs.JobNotFoundError):
        jobs.set_remote_type("greenhouse::does-not-exist", "remote")


def test_set_application_stage_accepts_numbered_scheme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    _insert_job()

    jobs.set_application_stage("greenhouse::123", "1. Phone Screen")

    assert jobs.get("greenhouse::123")["application_stage"] == "1. Phone Screen"


def test_set_application_stage_rejects_old_unnumbered_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_db(tmp_path, monkeypatch)
    _insert_job()

    with pytest.raises(jobs.InvalidStageError):
        jobs.set_application_stage("greenhouse::123", "Phone Screen")
