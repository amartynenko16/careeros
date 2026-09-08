"""Manual job intake: add a role Alex found himself (not from a scan) into
the same jobs table scanned roles live in, so it goes through the same
triage/tailor/build pipeline and is never tracked somewhere separate.

Two paths:
- add_from_url(): the URL is on a supported ATS (Ashby/Greenhouse/Lever) --
  fetch the single posting directly, same normalization scan.py applies
  (resolve_location, posted_at parsing).
- add_manual(): anything else (Workday, a company's own careers page,
  pasted JD text with no URL) -- caller supplies the fields directly.

Both run the job through the exact same deterministic filters scan.py uses
(job_matches_role_filter, geo_eligible) and report the result, rather than
silently accepting or rejecting -- the point is to give Alex (and the LLM
doing the qualitative read alongside this) a real, consistent answer, not
a second, looser set of rules for hand-found roles.
"""

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from careeros import db
from careeros.fetchers.base import geo_eligible, job_matches_role_filter, resolve_location
from careeros.scan import _normalize_posted_at

# Re-exported for anything importing geo_eligible from here (its original
# home, 2026-09-01, before it moved to fetchers/base.py on 2026-09-04 so
# scan.py could use it too without a circular import).
__all__ = ["IntakeError", "geo_eligible", "parse_url", "fetch_single_job", "add_from_url", "add_manual"]


class IntakeError(ValueError):
    pass


_URL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-fA-F-]{36})")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-fA-F-]{36})")),
]


def parse_url(url: str) -> tuple[str, str, str] | None:
    """Return (ats_type, company_slug, ats_job_id) if url matches a known
    ATS's posting URL pattern, else None."""
    for ats_type, pattern in _URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return ats_type, m.group(1), m.group(2)
    return None


def fetch_single_job(ats_type: str, company_slug: str, job_id: str) -> dict[str, Any] | None:
    """Fetch one posting's raw fields. Returns None if not found."""
    if ats_type == "greenhouse":
        r = httpx.get(
            f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}?content=true",
            timeout=30,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        from careeros.fetchers.greenhouse import _remote_type, _strip_html

        location = j.get("location", {}).get("name", "") or ""
        return {
            "ats_id": str(j.get("id", "")),
            "ats_url": j.get("absolute_url", ""),
            "title": j.get("title", "") or "",
            "location": location,
            "remote_type": _remote_type(location),
            "description": _strip_html(j.get("content", "") or ""),
            "updated_at": j.get("updated_at", "") or "",
        }

    if ats_type == "lever":
        r = httpx.get(
            f"https://api.lever.co/v0/postings/{company_slug}/{job_id}?mode=json", timeout=30
        )
        if r.status_code != 200:
            return None
        j = r.json()
        from careeros.fetchers.lever import _remote_type, _strip_html

        categories = j.get("categories", {}) or {}
        location = categories.get("location", "") or ""
        workplace_type = categories.get("commitment", "") or j.get("workplaceType", "")
        description = j.get("descriptionPlain") or _strip_html(j.get("description", "") or "")
        return {
            "ats_id": str(j.get("id", "")),
            "ats_url": j.get("hostedUrl", "") or j.get("applyUrl", ""),
            "title": j.get("text", "") or "",
            "location": location,
            "remote_type": _remote_type(location, workplace_type),
            "description": description,
            "updated_at": str(j.get("updatedAt", "") or ""),
        }

    if ats_type == "ashby":
        # No single-posting endpoint on the public board API; fetch the
        # whole board and find the matching id.
        r = httpx.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}?includeCompensation=true",
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        from careeros.fetchers.ashby import _remote_type, _strip_html

        for j in data.get("jobs", []):
            if j.get("id") == job_id:
                location = j.get("location", "") or ""
                is_remote = bool(j.get("isRemote", False))
                workplace_type = j.get("workplaceType", "") or ""
                description = _strip_html(j.get("descriptionHtml", "") or j.get("descriptionPlain", "") or "")
                return {
                    "ats_id": str(j.get("id", "")),
                    "ats_url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                    "title": j.get("title", "") or "",
                    "location": location,
                    "remote_type": _remote_type(location, is_remote, workplace_type),
                    "description": description,
                    "updated_at": str(j.get("publishedAt", "") or j.get("updatedAt", "") or ""),
                }
        return None

    return None


def _ensure_company_row(conn, company_slug: str, ats_type: str, now: str) -> None:
    existing = conn.execute("SELECT slug FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO companies (slug, name, tier, ats_type, ats_slug, notes, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                company_slug,
                company_slug.replace("-", " ").title(),
                None,
                ats_type if ats_type != "manual" else "unsupported",
                company_slug,
                "Added via `careeros jobs add` (a role Alex found himself), not part of the curated 43-company target list.",
                now,
            ),
        )


def _store_and_evaluate(ats_type: str, company_slug: str, raw: dict[str, Any]) -> dict[str, Any]:
    job_id = f"{ats_type}::{raw['ats_id']}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    location = resolve_location(raw.get("location", ""), raw.get("description", ""))
    posted_at = _normalize_posted_at(raw.get("updated_at", ""))
    title = raw.get("title", "")
    remote_type = raw.get("remote_type", "unknown")
    description = raw.get("description", "")

    role_ok = job_matches_role_filter(title, description)
    geo_ok = geo_eligible(location, title, remote_type, description)

    with db.connect() as conn:
        _ensure_company_row(conn, company_slug, ats_type, now)
        conn.execute(
            """
            INSERT INTO jobs (id, company_slug, ats_type, ats_url, title, location, remote_type, description, posted_at, fetched_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(id) DO UPDATE SET
                ats_url = excluded.ats_url,
                title = excluded.title,
                location = excluded.location,
                remote_type = excluded.remote_type,
                description = excluded.description,
                posted_at = excluded.posted_at,
                fetched_at = excluded.fetched_at
            """,
            (
                job_id, company_slug, ats_type, raw.get("ats_url", ""), title, location,
                remote_type, description, posted_at, now,
            ),
        )
        conn.commit()

    return {
        "job_id": job_id,
        "title": title,
        "company_slug": company_slug,
        "location": location,
        "remote_type": remote_type,
        "role_filter_pass": role_ok,
        "geo_eligible": geo_ok,
    }


def add_from_url(url: str) -> dict[str, Any]:
    parsed = parse_url(url)
    if parsed is None:
        raise IntakeError(
            "URL doesn't match a known ATS pattern (Ashby/Greenhouse/Lever). "
            "Paste the job description text instead (add_manual)."
        )
    ats_type, company_slug, job_id = parsed
    raw = fetch_single_job(ats_type, company_slug, job_id)
    if raw is None:
        raise IntakeError(f"could not fetch job {job_id!r} from the {ats_type} board {company_slug!r}")
    return _store_and_evaluate(ats_type, company_slug, raw)


def add_manual(
    company_slug: str,
    title: str,
    description: str,
    location: str = "",
    remote_type: str = "unknown",
    ats_url: str = "",
) -> dict[str, Any]:
    """For roles with no supported-ATS URL (Workday, a custom careers page,
    pasted text with nothing to fetch). ats_id is derived from a hash of
    the content so re-adding the same posting updates it rather than
    duplicating."""
    import hashlib

    digest = hashlib.sha256(f"{company_slug}|{title}|{description[:200]}".encode()).hexdigest()[:16]
    raw = {
        "ats_id": f"manual-{digest}",
        "ats_url": ats_url,
        "title": title,
        "location": location,
        "remote_type": remote_type,
        "description": description,
        "updated_at": "",
    }
    return _store_and_evaluate("manual", company_slug, raw)
