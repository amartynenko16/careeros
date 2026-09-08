"""Integration test for scan.py.

Uses a temp DB, seeds companies, monkeypatches fetchers to return canned data,
verifies filter + upsert + status preservation.
"""

from pathlib import Path

import pytest

from careeros import companies, db, jobs, scan
from careeros.fetchers.base import RawJob


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(companies, "COMPANIES_YAML_PATH", tmp_path / "companies.yaml")
    db.init_db()


def _seed_companies(tmp_path: Path) -> None:
    (tmp_path / "companies.yaml").write_text(
        """
companies:
  - slug: alpha
    name: Alpha
    tier: 1
    ats_type: greenhouse
    ats_slug: alpha
    notes: ""
  - slug: bravo
    name: Bravo
    tier: 2
    ats_type: unsupported
    ats_slug: ""
    notes: "Workday"
""",
        encoding="utf-8",
    )
    companies.load_from_yaml()


def test_scan_filters_and_upserts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _seed_companies(tmp_path)

    # First scan: 3 postings, 2 pass the role filter
    call_counter = {"n": 0}

    def fake_gh(slug: str) -> list[RawJob]:
        call_counter["n"] += 1
        return [
            RawJob(ats_id="1", ats_url="https://x/1", title="Solutions Consultant", location="Remote", remote_type="remote", description="A", department="CS", updated_at=""),
            RawJob(ats_id="2", ats_url="https://x/2", title="Backend Engineer", location="NYC", remote_type="onsite", description="B", department="Eng", updated_at=""),
            RawJob(ats_id="3", ats_url="https://x/3", title="Implementation Manager", location="Hybrid Toronto", remote_type="hybrid", description="C", department="PS", updated_at=""),
        ]

    monkeypatch.setitem(scan.FETCHERS, "greenhouse", fake_gh)

    summary = scan.scan()
    alpha = next(c for c in summary.per_company if c.slug == "alpha")
    assert alpha.fetched == 3
    assert alpha.matched == 2
    assert alpha.inserted == 2

    # Bravo is unsupported and shouldn't appear as an error entry
    assert not any(c.slug == "bravo" for c in summary.per_company)
    assert any("Bravo" in s for s in summary.skipped)

    # Second scan with the same data: should be all unchanged
    summary2 = scan.scan()
    alpha2 = next(c for c in summary2.per_company if c.slug == "alpha")
    assert alpha2.inserted == 0
    assert alpha2.unchanged == 2


def test_scan_preserves_user_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _seed_companies(tmp_path)

    def fake_gh(slug: str) -> list[RawJob]:
        return [
            RawJob(ats_id="1", ats_url="https://x/1", title="Solutions Consultant", location="Remote", remote_type="remote", description="Original", department="", updated_at=""),
        ]

    monkeypatch.setitem(scan.FETCHERS, "greenhouse", fake_gh)
    scan.scan()

    # User marks it saved
    jobs.set_status("greenhouse::1", "saved")

    # Re-scan with an updated description
    def fake_gh_updated(slug: str) -> list[RawJob]:
        return [
            RawJob(ats_id="1", ats_url="https://x/1", title="Solutions Consultant", location="Remote", remote_type="remote", description="Updated", department="", updated_at=""),
        ]

    monkeypatch.setitem(scan.FETCHERS, "greenhouse", fake_gh_updated)
    scan.scan()

    job = jobs.get("greenhouse::1")
    assert job["description"] == "Updated"
    assert job["status"] == "saved"   # user status preserved


def test_scan_filters_geo_ineligible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _seed_companies(tmp_path)

    def fake_gh(slug: str) -> list[RawJob]:
        return [
            # Passes role filter but is onsite outside the Ancaster area: excluded.
            RawJob(ats_id="1", ats_url="https://x/1", title="Solutions Consultant", location="Austin, Texas", remote_type="onsite", description="A", department="CS", updated_at=""),
            # Passes role filter, remote with no region restriction: kept.
            RawJob(ats_id="2", ats_url="https://x/2", title="Solutions Consultant", location="Remote", remote_type="remote", description="B", department="CS", updated_at=""),
            # Passes role filter, remote but US-work-authorization restricted per the ATS location field: excluded.
            RawJob(ats_id="3", ats_url="https://x/3", title="Solutions Consultant", location="Remote, Colorado, United States", remote_type="remote", description="C", department="CS", updated_at=""),
        ]

    monkeypatch.setitem(scan.FETCHERS, "greenhouse", fake_gh)

    summary = scan.scan()
    alpha = next(c for c in summary.per_company if c.slug == "alpha")
    assert alpha.matched == 3        # all 3 pass the role-keyword filter
    assert alpha.geo_rejected == 2   # Austin onsite + Colorado-restricted remote
    assert alpha.inserted == 1

    assert jobs.get("greenhouse::2") is not None
    with pytest.raises(jobs.JobNotFoundError):
        jobs.get("greenhouse::1")
    with pytest.raises(jobs.JobNotFoundError):
        jobs.get("greenhouse::3")


def test_scan_records_fetcher_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    _seed_companies(tmp_path)

    def broken(slug: str) -> list[RawJob]:
        raise RuntimeError("network down")

    monkeypatch.setitem(scan.FETCHERS, "greenhouse", broken)
    summary = scan.scan()
    alpha = next(c for c in summary.per_company if c.slug == "alpha")
    assert "network down" in alpha.error
    assert alpha.fetched == 0
