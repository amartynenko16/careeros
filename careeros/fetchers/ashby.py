"""Ashby public job board API fetcher.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Ashby's public API doesn't require auth for job board queries.
"""

import html
import re

import httpx

from careeros.fetchers.base import RawJob


BASE = "https://api.ashbyhq.com/posting-api/job-board"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _remote_type(location: str, is_remote: bool, workplace_type: str) -> str:
    """Ashby exposes isRemote and workplaceType hints; use them first."""
    if is_remote:
        return "remote"
    if workplace_type:
        wt = workplace_type.lower()
        if wt in ("remote", "hybrid", "onsite", "on-site"):
            return "onsite" if wt == "on-site" else wt
    if not location:
        return "unknown"
    lo = location.lower()
    if "remote" in lo:
        return "remote"
    if "hybrid" in lo:
        return "hybrid"
    return "onsite"


def fetch(slug: str) -> list[RawJob]:
    """Fetch all live Ashby postings for a company."""
    url = f"{BASE}/{slug}?includeCompensation=true"
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()

    jobs: list[RawJob] = []
    for job in data.get("jobs", []):
        location = job.get("location", "") or ""
        is_remote = bool(job.get("isRemote", False))
        workplace_type = job.get("workplaceType", "") or ""
        team = job.get("team", "") or job.get("department", "") or ""
        description = _strip_html(job.get("descriptionHtml", "") or job.get("descriptionPlain", "") or "")

        jobs.append(
            RawJob(
                ats_id=str(job.get("id", "")),
                ats_url=job.get("jobUrl", "") or job.get("applyUrl", ""),
                title=job.get("title", "") or "",
                location=location,
                remote_type=_remote_type(location, is_remote, workplace_type),
                description=description,
                department=team,
                updated_at=str(job.get("publishedAt", "") or job.get("updatedAt", "") or ""),
            )
        )
    return jobs
