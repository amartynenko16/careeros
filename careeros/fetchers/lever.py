"""Lever public postings API fetcher.

Docs: https://help.lever.co/hc/en-us/articles/360003230291-Applying-to-Lever
Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
"""

import html
import re

import httpx

from careeros.fetchers.base import RawJob


BASE = "https://api.lever.co/v0/postings"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _remote_type(location: str, workplace_type: str) -> str:
    """Prefer Lever's workplace_type field; fall back to string sniffing."""
    if workplace_type:
        wt = workplace_type.lower()
        if wt in ("remote", "hybrid", "on-site"):
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
    """Fetch all live Lever postings for a company."""
    url = f"{BASE}/{slug}?mode=json"
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()

    jobs: list[RawJob] = []
    for post in data:
        # Lever returns a flat list. Extract location as best possible.
        categories = post.get("categories", {}) or {}
        location = categories.get("location", "") or ""
        team = categories.get("team", "") or categories.get("department", "") or ""
        workplace_type = categories.get("commitment", "") or post.get("workplaceType", "")

        # Description is under `descriptionPlain` or `description`.
        description = post.get("descriptionPlain") or _strip_html(post.get("description", "") or "")

        jobs.append(
            RawJob(
                ats_id=str(post.get("id", "")),
                ats_url=post.get("hostedUrl", "") or post.get("applyUrl", ""),
                title=post.get("text", "") or "",
                location=location,
                remote_type=_remote_type(location, workplace_type),
                description=description,
                department=team,
                updated_at=str(post.get("updatedAt", "") or ""),
            )
        )
    return jobs
