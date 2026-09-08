"""Scan orchestrator: for each supported company, fetch open postings, filter by
role keywords and geo eligibility, and upsert into the jobs table.

Design:
- ATS calls are independent; failures for one company do not stop the run.
- Jobs are keyed by a composite `id` = `{ats_type}::{ats_id}` so re-scans upsert.
- Preserves user-set `status` (saved, rejected, applied) across re-scans.
- Scoring lives in a separate module; scan only fetches and stores.

Geo filtering (added 2026-09-04): role-keyword filtering alone let through
roles with hard geo/work-authorization blockers a title can't reveal --
explicit "must be physically located in the United States" clauses, an
"Available Locations: Austin, Texas" buried in description text under a
generic "Hybrid" ATS field, etc. geo_eligible() (shared with intake.py's
manual add path, lives in fetchers/base.py) now runs against each job's
RESOLVED location before it's ever inserted, same as the role filter: a
geo-ineligible job is never stored, not stored-then-rejected. This means a
job that fails geo can't linger as visible clutter, but it also means if
you ever want to review what got excluded on geo grounds, that data isn't
sitting in the jobs table -- geo-eligibility is a hard pre-filter, not a
triage state.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from careeros import companies, db
from careeros.fetchers import ashby, greenhouse, lever
from careeros.fetchers.base import RawJob, geo_eligible, job_matches_role_filter, resolve_location


FETCHERS: dict[str, Callable[[str], list[RawJob]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


@dataclass
class CompanyResult:
    slug: str
    name: str
    fetched: int = 0        # total postings returned by the ATS
    matched: int = 0        # postings surviving role-keyword filter
    geo_rejected: int = 0   # postings surviving role filter but failing geo_eligible
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    error: str = ""


@dataclass
class ScanSummary:
    per_company: list[CompanyResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # unsupported ATSs
    warnings: list[str] = field(default_factory=list)

    @property
    def total_matched(self) -> int:
        return sum(c.matched for c in self.per_company)

    @property
    def total_geo_rejected(self) -> int:
        return sum(c.geo_rejected for c in self.per_company)

    @property
    def total_new(self) -> int:
        return sum(c.inserted for c in self.per_company)


# --- Upsert ------------------------------------------------------------------

# Columns overwritten from the ATS on every scan.
_ATS_COLUMNS = (
    "company_slug",
    "ats_type",
    "ats_url",
    "title",
    "location",
    "remote_type",
    "description",
    "posted_at",
    "fetched_at",
)


def _job_id(ats_type: str, ats_id: str) -> str:
    return f"{ats_type}::{ats_id}"


def _normalize_posted_at(raw: str) -> str:
    """Normalize an ATS's posting/update timestamp to ISO 8601 UTC.

    Greenhouse and Ashby return ISO datetime strings; Lever returns epoch
    milliseconds as a string. Returns "" if the value can't be parsed rather
    than raising, since a missing date shouldn't fail the whole scan.
    """
    if not raw:
        return ""
    if raw.isdigit():
        try:
            millis = int(raw)
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def _upsert_job(
    company: dict[str, Any],
    job: RawJob,
    now: str,
    result: CompanyResult,
) -> None:
    """Insert a new job or update an existing one. Preserves user status columns."""
    job_id = _job_id(company["ats_type"], job.get("ats_id", ""))
    if not job.get("ats_id"):
        return  # can't dedup without an ATS ID; skip

    row = {
        "id": job_id,
        "company_slug": company["slug"],
        "ats_type": company["ats_type"],
        "ats_url": job.get("ats_url", ""),
        "title": job.get("title", ""),
        "location": resolve_location(job.get("location", ""), job.get("description", "")),
        "remote_type": job.get("remote_type", "unknown"),
        "description": job.get("description", ""),
        "posted_at": _normalize_posted_at(job.get("updated_at", "")),
        "fetched_at": now,
    }

    with db.connect() as conn:
        existing = conn.execute(
            "SELECT " + ", ".join(_ATS_COLUMNS) + " FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

        if existing is None:
            cols = ["id"] + list(_ATS_COLUMNS)
            placeholders = ", ".join(["?"] * len(cols))
            values = [row[c] for c in cols]
            conn.execute(
                f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            result.inserted += 1
        else:
            changed = any(existing[c] != row[c] for c in _ATS_COLUMNS if c != "fetched_at")
            set_clause = ", ".join(f"{c} = ?" for c in _ATS_COLUMNS)
            values = [row[c] for c in _ATS_COLUMNS] + [job_id]
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                values,
            )
            if changed:
                result.updated += 1
            else:
                result.unchanged += 1

        conn.commit()


# --- Scan --------------------------------------------------------------------

def scan(only_slug: str | None = None) -> ScanSummary:
    """Run a scan across all supported companies (or one, if only_slug is given)."""
    summary = ScanSummary()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    supported = companies.all_supported()
    if only_slug:
        supported = [c for c in supported if c["slug"] == only_slug]
        if not supported:
            summary.warnings.append(f"No supported company found with slug '{only_slug}'.")
            return summary

    for company in supported:
        result = CompanyResult(slug=company["slug"], name=company["name"])
        summary.per_company.append(result)

        fetcher = FETCHERS.get(company["ats_type"])
        if fetcher is None:
            result.error = f"unknown ats_type '{company['ats_type']}'"
            continue

        try:
            raw_jobs = fetcher(company["ats_slug"])
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            continue

        result.fetched = len(raw_jobs)
        for job in raw_jobs:
            if not job_matches_role_filter(job.get("title", ""), job.get("description", "")):
                continue
            result.matched += 1

            resolved_location = resolve_location(job.get("location", ""), job.get("description", ""))
            if not geo_eligible(
                resolved_location, job.get("title", ""), job.get("remote_type", "unknown"), job.get("description", "")
            ):
                result.geo_rejected += 1
                continue

            _upsert_job(company, job, now, result)

    # Record what we skipped so `scan` output is honest about coverage gaps.
    for company in companies.all_unsupported():
        summary.skipped.append(f"{company['name']} ({company['ats_type'] or 'no ats'})")

    _record_scan_state(summary, now)
    return summary


def _record_scan_state(summary: ScanSummary, now: str) -> None:
    """Persist last-scan metadata to sync_state."""
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
            ("jobs_last_scanned_at", now, now),
        )
        conn.commit()
