"""Greenhouse Job Board API fetcher.

Docs: https://developers.greenhouse.io/job-board.html
Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Returns all live postings for a company; scan filters by role keywords.
"""

import html
import re

import httpx

from careeros.fetchers.base import RawJob


BASE = "https://boards-api.greenhouse.io/v1/boards"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """Remove HTML tags and unescape entities. Cheap, not perfect."""
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _remote_type(location: str) -> str:
    """Best-effort remote/hybrid/onsite classification from a location string."""
    if not location:
        return "unknown"
    lo = location.lower()
    if "remote" in lo:
        return "remote"
    if "hybrid" in lo:
        return "hybrid"
    return "onsite"


def fetch(slug: str) -> list[RawJob]:
    """Fetch all live Greenhouse postings for a company. Raises on HTTP errors."""
    url = f"{BASE}/{slug}/jobs?content=true"
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()

    jobs: list[RawJob] = []
    for job in data.get("jobs", []):
        location = job.get("location", {}).get("name", "") or ""
        departments = job.get("departments", []) or []
        dept = departments[0].get("name", "") if departments else ""

        jobs.append(
            RawJob(
                ats_id=str(job.get("id", "")),
                ats_url=job.get("absolute_url", ""),
                title=job.get("title", "") or "",
                location=location,
                remote_type=_remote_type(location),
                description=_strip_html(job.get("content", "") or ""),
                department=dept,
                updated_at=job.get("updated_at", "") or "",
            )
        )
    return jobs
