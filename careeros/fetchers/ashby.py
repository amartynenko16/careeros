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


# Currency prefix is optional and may carry a 2-3 letter code before the $
# (Ashby renders Canadian figures as "CA$185K", not just "$185K"). The
# figure itself may carry a decimal (Docebo's TAM posting: "CA$100.7K -
# CA$134.2K", found 2026-09-15) -- the digit group must allow one, or the
# whole regex fails to match and the fallback returns the raw, unreformatted
# text (with its "CA$" prefix and no established-convention wording at all).
_COMP_RANGE_RE = re.compile(
    r"(?:[A-Z]{2,3}\$|\$)?\s*([\d,]+(?:\.\d+)?)\s*[KkMm]?\s*[-–]\s*(?:[A-Z]{2,3}\$|\$)?\s*([\d,]+(?:\.\d+)?)\s*([KkMm])"
)


def _format_comp_for_notion(tier_summary: str) -> str:
    """Reformat Ashby's raw compensation text into Alex's established
    Notion Comp convention (surveyed from existing rows 2026-09-12):
    "Posted: Base unconfirmed ($X-YK OTE)" when the figure is presented as
    on-target earnings with no separate base disclosed (matches Sentry,
    Ramp, Vantage Analytics, Brex, Tailscale, Decagon, Docker), or "Posted:
    $X-YK base (OTE unconfirmed)" for a plain base-only range (matches
    Ashby[company], 1Password). A landed/negotiated figure prepended before
    "Posted:" is always a manual, later edit (`jobs set_comp()`), never
    something a fetcher writes.

    Whether a figure is "OTE" depends on which of Ashby's two summary
    strings it came from: the tier-level `tierSummary` includes the actual
    qualifier word (e.g. "OTE $185K - $222K"), but the compensation
    object's own top-level `compensationTierSummary` silently drops it
    (found 2026-09-11 on Airbyte's TAM posting: top-level string was "CA$185K
    - CA$222K - Offers Equity - Offers Commission", no "OTE" anywhere, while
    the tier's own tierSummary for the exact same figure was "OTE CA$185K -
    CA$222K - ..." -- the qualifier only survives in the per-tier text).
    Callers should pass the tier-level tierSummary, not the top-level one.

    Anything that doesn't clearly match either shape (e.g. base AND OTE
    both explicitly broken out) falls back to the raw text rather than
    guessing at a format with no real precedent among existing rows yet --
    check current Notion rows again if a new shape shows up often enough
    to be worth encoding here.
    """
    if not tier_summary:
        return ""
    m = _COMP_RANGE_RE.search(tier_summary)
    if not m:
        return f"Posted: {tier_summary}"
    low, high, unit = m.group(1).replace(",", ""), m.group(2).replace(",", ""), m.group(3).upper()
    range_str = f"${low}-{high}{unit}"
    if re.search(r"\bOTE\b", tier_summary, re.IGNORECASE):
        return f"Posted: Base unconfirmed ({range_str} OTE)"
    return f"Posted: {range_str} base (OTE unconfirmed)"


def _compensation_summary(job: dict) -> str:
    """Ashby returns a structured `compensation` object when the request
    includes includeCompensation=true (which this fetcher already passes),
    but until 2026-09-11 nothing ever read it -- a real gap found when a
    posting's own careers page showed a compensation field that wasn't in
    the description text at all (it's a separate structured field some
    ATS-hosted career pages render alongside the description, not embedded
    in it), so scanning the description could never have found it
    regardless of how carefully it's read. Companies can turn this display
    off per-posting (shouldDisplayCompensationOnJobPostings, a top-level
    field on the job, not nested in `compensation`) -- respect that rather
    than surfacing a figure the company chose to hide.

    Prefers the first compensation tier's own tierSummary over the
    compensation object's top-level compensationTierSummary -- see
    _format_comp_for_notion's docstring for why (the "OTE" qualifier only
    survives in the tier-level text)."""
    if not job.get("shouldDisplayCompensationOnJobPostings", True):
        return ""
    comp = job.get("compensation") or {}
    tiers = comp.get("compensationTiers") or []
    raw = (tiers[0].get("tierSummary") if tiers else None) or comp.get("compensationTierSummary", "") or ""
    return _format_comp_for_notion(raw)


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
                comp=_compensation_summary(job),
            )
        )
    return jobs
