"""Common types and helpers for ATS fetchers.

Each fetcher is a callable that takes a company's ATS slug and returns a list
of RawJob dicts. The scan orchestrator handles filtering, dedup, and DB
persistence; fetchers stay narrow.
"""

import re
from typing import TypedDict


class RawJob(TypedDict, total=False):
    """One posting as returned by a fetcher, before role filtering.

    Keys marked total=False are optional; ats_url and title are required at the
    fetcher level.
    """

    ats_id: str          # unique posting ID within the ATS
    ats_url: str         # canonical public URL of the posting
    title: str           # job title as posted
    location: str        # location string as posted
    remote_type: str     # "remote", "hybrid", "onsite", or "unknown"
    description: str     # full posting body (may be HTML or plain text)
    department: str      # department or team, if provided
    updated_at: str      # ISO datetime string if the ATS exposes one


# Positive role keywords: title must contain at least one of these to survive
# fetch-time filtering. Match is case-insensitive substring.
ROLE_KEYWORDS: tuple[str, ...] = (
    # Post-sales technical
    "solutions consultant",
    "solutions engineer",
    "solutions architect",
    "solution architect",         # singular variant (e.g. Microsoft's own titling), same role
    "solution engineer",
    "technical account manager",
    "customer solutions engineer",
    "customer solutions architect",
    "customer success architect",
    "technical customer success",
    "technical consultant",
    "technical success manager",
    # Implementation and delivery
    "implementation manager",
    "implementation consultant",
    "implementation engineer",
    "professional services manager",
    "professional services consultant",
    "professional services engineer",
    "delivery manager",
    "onboarding manager",
    # Field / FDE
    "forward deployed engineer",
    "forward deployment",         # e.g. "Forward Deployment Strategist" (Maxima) -- same function, different title convention
    "field engineer",
    # Program roles adjacent to Alex's fit
    "program manager, customer",
    "customer engineer",
    # CSA and TAM variants
    "csa,",  # trailing comma catches "CSA, ..."
    " csa ",  # padded catches " CSA " inside title
    "tam,",
    " tam ",
)


# Hard-negative keywords: title containing any of these is excluded outright.
NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "sdr",
    "bdr",
    "account executive",
    "channel partner",
    "engineering manager",
    "director of engineering",
    "vp of engineering",
    "senior software engineer",
    "staff software engineer",
    "sales engineer",           # pre-sales at Datadog and similar; excluded per user's stated criteria
    "principal sales engineer",
    "pre-sales",                 # explicit pre-sales titles; excluded per user's stated criteria
    "presales",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "full-stack engineer",
    "data engineer",
    "machine learning engineer",
    "ml engineer",
    "site reliability engineer",
    "product designer",
    "recruiter",
    "intern",
    "internship",
    # Sales roles that are pure sales
    "sales director",
    "sales manager",
    # Executive / too-senior for a 7-year-experience IC search. Alex has been
    # explicit: no management/leadership roles, IC only.
    "chief ",
    "vp,",
    "vp of",
    "svp",
    "evp",
    "principal ",
    "director",
    "head of",
    "distinguished",
    # Irrelevant function that "delivery manager"/"program manager" keywords
    # incidentally catch (e.g. Anthropic's "Data Center Capacity Delivery
    # Manager" -- data center operations, not customer-facing delivery).
    "data center",
)

# Description-level pre-sales signals. Some pre-sales roles use ambiguous
# titles that pass ROLE_KEYWORDS (e.g. PagerDuty's "Solutions Consultant",
# which the JD itself describes as sitting "at the nexus between pre-sales,
# sales, and customer experience"). Title-only filtering can't catch these;
# this scans the description text as a second pass. Kept narrow and
# high-precision -- phrases a role would use to self-describe as pre-sales.
#
# Deliberately excludes bare "pre-sales"/"presales": genuine post-sales JDs
# routinely mention pre-sales in candidate-background bullets ("ideally from
# a company with a split between pre-sales SA and post-sales technical
# ownership" -- a real Vercel TAM posting), which is describing the role AS
# post-sales, not as pre-sales. That phrase is too common in post-sales JDs
# to use as a negative signal; the phrases below are specific enough that a
# genuinely post-sales role is unlikely to use them to describe itself.
NEGATIVE_DESCRIPTION_KEYWORDS: tuple[str, ...] = (
    "technical salesperson",
    "sales cycle",
    "sales cycles",
    "quota-carrying",
    "quota carrying",
)


# Matches "Manager, <team/scope>" or "Senior Manager, <team/scope>" at the
# START of a title -- GitLab's people-leadership title format (e.g. "Manager,
# Solutions Architects", "Senior Manager, Solutions Architecture, AI & Dev
# Platform"). Deliberately anchored to the start so it doesn't collide with
# legitimate IC-adjacent titles like "Program Manager, Customer Success"
# (a positive ROLE_KEYWORD), where "Manager" isn't the title's lead word.
_LEADERSHIP_TITLE_RE = re.compile(r"^(senior |sr\.?\s+)?manager,\s")


def title_matches_role_filter(title: str) -> bool:
    """Return True if title survives positive-and-negative keyword filtering."""
    # Normalize hyphens to spaces on BOTH sides of the comparison, so
    # "Forward-Deployed Engineer" matches "forward deployed engineer" and a
    # hyphenated negative keyword like "pre-sales" still catches a hyphenated
    # title. Normalizing only the title (an earlier version of this function
    # did) silently broke "pre-sales" as a negative keyword: the input no
    # longer contained a hyphen to match against. Normalizing the keyword
    # list too closes that whole class of bug rather than requiring a
    # manually-added space-variant for every hyphenated keyword.
    t = f" {title.lower().replace('-', ' ')} "
    if any(neg.replace("-", " ") in t for neg in NEGATIVE_KEYWORDS):
        return False
    if _LEADERSHIP_TITLE_RE.match(title.strip().lower()):
        return False
    return any(kw.replace("-", " ") in t for kw in ROLE_KEYWORDS)


_PRESALES_MENTION_RE = re.compile(r"pre-?sales")


def description_indicates_presales(description: str) -> bool:
    """Return True if the job description self-describes as pre-sales.

    A second-pass filter for roles whose title alone looks post-sales but
    whose actual description reveals pre-sales/quota-carrying scope.

    Bare "pre-sales"/"presales" mentions are checked against whether
    "post-sales" appears ANYWHERE else in the description, not just nearby:
    genuine post-sales JDs use "pre-sales" all over the place in ways that
    don't sit next to the word "post-sales" -- a candidate-background
    contrast ("ideally from a company with a split between pre-sales SA and
    post-sales technical ownership" -- Vercel), a handoff boundary ("ensure
    smooth handoff from pre-sales to onboarding" -- Docker, ~500 characters
    from that JD's own repeated "post-sales lifecycle" self-description), an
    org-structure mention ("unifies pre- and post-sales technical teams" --
    also Docker). A genuinely self-describing pre-sales JD (Cloudflare:
    "Pre-Sales Solution Engineering organisation is responsible for...";
    LaunchDarkly: "...throughout the pre-sales lifecycle") never mentions
    "post-sales" at all, anywhere in the document -- confirmed empirically
    against three real postings before narrowing this from a per-mention
    proximity window to a whole-document check.
    """
    if not description:
        return False
    d = description.lower()
    if any(kw in d for kw in NEGATIVE_DESCRIPTION_KEYWORDS):
        return True
    if "post-sales" in d or "post sales" in d:
        return False
    return bool(_PRESALES_MENTION_RE.search(d))


def job_matches_role_filter(title: str, description: str = "") -> bool:
    """Full role match: title keywords, then a description-level presales check."""
    if not title_matches_role_filter(title):
        return False
    return not description_indicates_presales(description)


# ATS location fields that carry no real geo information (seen from
# Cloudflare's Greenhouse board: "Hybrid" and "Distributed" say nothing about
# which office). When the structured field is one of these, the real
# location -- if the posting states one at all -- is buried in description
# text, labeled inconsistently ("Available Location:", "Job Location:",
# plain "Location:"). Kept as a narrow fallback, not a blanket pass: when the
# structured field is already specific (e.g. "Toronto, ON"), it's trusted
# as-is and description text is never parsed.
INCONCLUSIVE_LOCATIONS: frozenset[str] = frozenset(
    {"", "hybrid", "distributed", "remote", "onsite", "on-site", "unknown", "n/a", "tbd"}
)

_LOCATION_LABEL_RE = re.compile(
    r"(?:available locations?|job location|primary location|location)\s*:\s*([^<\n]{1,120})",
    re.IGNORECASE,
)


def resolve_location(location: str, description: str) -> str:
    """Fall back to a location line embedded in the description, but only
    when the ATS's structured location field is blank or too generic to be
    useful. Returns the original value unchanged otherwise."""
    if (location or "").strip().lower() not in INCONCLUSIVE_LOCATIONS:
        return location
    if not description:
        return location
    match = _LOCATION_LABEL_RE.search(description)
    if not match:
        return location
    extracted = match.group(1).strip(" \t-–—")
    return extracted or location


# Your geo criteria (see CLAUDE.md's "Job search criteria" section): edit this
# list to your own commute area -- hybrid/onsite roles are only eligible if
# their location matches one of these terms, and genuinely open remote is
# handled separately below. Lives here (not in intake.py, where the geo-filter
# logic originated) because scan.py needs it too (a role-keyword-only filter
# can't catch US-work-authorization-restricted or hard-onsite-elsewhere
# roles) and fetchers/base.py is the one shared module both scan.py and
# intake.py already import from without creating a circular import (intake.py
# imports _normalize_posted_at from scan.py, so scan.py importing from
# intake.py would cycle). This is the one copy; intake.py re-exports it.
TARGET_GEO_AREA = ["toronto", "cambridge", "waterloo", "kitchener", "hamilton", "ancaster", "ontario"]
OPEN_REMOTE_TERMS = ["global", "worldwide", "anywhere", "canada", "north america"]

# ATS region-tag segments (comma-separated location field, e.g. "Remote,
# AMER") that mean the company's general Americas remote-hiring region,
# which includes Canada. Matched as a whole comma-separated segment, not a
# bare substring -- "amer" as a substring also appears inside unrelated
# words (e.g. "Cameroon"), which a segment match avoids.
OPEN_REGION_SEGMENTS = {"amer", "americas"}

# Narrow, high-precision phrases a JD uses to hard-restrict a remote role to
# physical US residency/timezone -- distinct from a merely-US-HQ'd company
# mentioning "United States" in a benefits/compensation boilerplate section
# (e.g. Cloudflare: "benefits for employees in the United States..."),
# which is not a restriction and must not match. Found empirically
# 2026-09-04: Fivetran ("must be physically located in the United States")
# and Temporal ("Reside within the Eastern time zone (United States)") both
# had ATS location fields that looked open ("Remote, ..., AMER") or merely
# named a hiring-manager's city ("Boston, Massachusetts") -- the real
# restriction only showed up in the description body.
_US_ONLY_RESTRICTION_RE = re.compile(
    r"must\s+(?:be\s+)?(?:physically\s+)?(?:located|reside|residing)\s+in\s+the\s+united\s+states"
    r"|reside\s+within\s+the\s+[a-z]+\s+time\s*zone\s*\(\s*united\s+states\s*\)",
    re.IGNORECASE,
)


def description_indicates_us_only(description: str) -> bool:
    """Return True if the JD body itself hard-restricts to US physical
    residency/timezone, regardless of what the location field implies."""
    if not description:
        return False
    return bool(_US_ONLY_RESTRICTION_RE.search(description))


def geo_eligible(location: str, title: str, remote_type: str, description: str = "") -> bool:
    """Apply against the RESOLVED location (post resolve_location()), not the
    raw ATS field -- a raw "Hybrid"/"Distributed" value tells you nothing;
    the real city is often only recoverable from resolve_location()'s
    description-text fallback (e.g. Cloudflare's FDE posting: ATS field said
    "Hybrid", description said "Austin, Texas, US").

    description is optional (older callers may omit it) but should be passed
    when available: a hard residency restriction stated in the JD body
    overrides whatever the location field implies, since an ATS region tag
    like "AMER" describes the company's general remote-hiring region, not a
    per-role guarantee -- see description_indicates_us_only.
    """
    if description_indicates_us_only(description):
        return False
    loc = (location or "").lower().strip()
    loc_segments = [s.strip() for s in loc.split(",")]
    title_l = (title or "").lower()
    if any(t in title_l for t in TARGET_GEO_AREA):
        return True
    if remote_type in ("hybrid", "onsite"):
        return any(t in loc for t in TARGET_GEO_AREA)
    if remote_type == "remote":
        if any(t in loc for t in OPEN_REMOTE_TERMS) or any(t in loc for t in TARGET_GEO_AREA):
            return True
        if any(seg in OPEN_REGION_SEGMENTS for seg in loc_segments):
            return True
        return loc in ("", "remote")
    return any(t in loc for t in TARGET_GEO_AREA)
