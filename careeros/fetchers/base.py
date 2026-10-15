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
    comp: str            # disclosed compensation, exactly as the ATS states it; only
                          # populated where the ATS exposes a real structured field
                          # (currently just Ashby's compensationTierSummary) -- never
                          # scraped or inferred from free-text description


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
    "engagement manager",         # customer-facing PS/delivery role, e.g. Replicant's "Senior Engagement Manager" (posted as "Agent PM" -- see fetchers/base.py module notes on title/body mismatches)
    # Field / FDE
    "forward deployed engineer",
    "forward deployment",         # e.g. "Forward Deployment Strategist" (Maxima) -- same function, different title convention
    "field engineer",
    # Program roles adjacent to this role family
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
    # Executive / too-senior for an individual-contributor search. Excluded
    # per the user's stated criteria: no management/leadership roles, IC only.
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
    "data centre",               # British/Canadian spelling, same false positive
    # Irrelevant function that "engagement manager" incidentally catches:
    # public policy/community relations, not customer-facing delivery (e.g.
    # Anthropic's "Community Engagement Manager, Data Centres (Canada)",
    # surfaced the same day "engagement manager" was added, 2026-10-02).
    "community engagement",
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


def _title_hits_negative(title: str) -> bool:
    """Shared by title_matches_role_filter and job_matches_role_filter's
    rescue pass below -- a negative-keyword or leadership-pattern hit is a
    hard exclude in both, never overridable by description content."""
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
        return True
    return bool(_LEADERSHIP_TITLE_RE.match(title.strip().lower()))


def _title_hits_positive(title: str) -> bool:
    t = f" {title.lower().replace('-', ' ')} "
    return any(kw.replace("-", " ") in t for kw in ROLE_KEYWORDS)


def title_matches_role_filter(title: str) -> bool:
    """Return True if title survives positive-and-negative keyword filtering."""
    if _title_hits_negative(title):
        return False
    return _title_hits_positive(title)


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


# Narrow, high-precision phrases describing the actual FUNCTION of a
# customer-facing PS/delivery role (Implementation Manager/Engagement
# Manager shape), for rescuing postings whose TITLE doesn't say so. Found
# empirically 2026-10-01: Replicant posted a "Senior Engagement Manager"
# role (professional services account ownership, running deployment
# projects, implementation methodology) under the title "Agent PM" --
# "engagement manager" is now in ROLE_KEYWORDS too, but titles can mismatch
# their own body in ways no fixed keyword list will fully anticipate (see
# also GitLab the same week: "Professional Services Engineer - West" title,
# "Professional Services Technical Architect" body). This is deliberately
# narrow -- each phrase is specific enough to the delivery-ownership
# function that an unrelated JD (e.g. a generic PM or eng role) is unlikely
# to use it incidentally.
_ENGAGEMENT_DELIVERY_SHAPE_RE = re.compile(
    r"professional services account owner"
    r"|engagement advisor"
    r"|manag(?:e|ing)\s+deployment projects"
    r"|implementation methodolog",
    re.IGNORECASE,
)


def description_indicates_engagement_delivery_shape(description: str) -> bool:
    """Return True if the JD body describes an Implementation/Engagement
    Manager-shaped delivery role, regardless of what the title says."""
    if not description:
        return False
    return bool(_ENGAGEMENT_DELIVERY_SHAPE_RE.search(description))


def job_matches_role_filter(title: str, description: str = "") -> bool:
    """Full role match: title keywords, then a description-level presales
    check -- plus a rescue pass for an AMBIGUOUS title (one that hit neither
    a positive nor a negative keyword, e.g. "Agent PM") whose JD body
    describes this role family anyway (see
    description_indicates_engagement_delivery_shape). A title that hits a
    NEGATIVE keyword (e.g. "Director of Engineering", "Sales Engineer") is
    excluded outright regardless of description content -- the rescue only
    ever fires for titles the filter had no opinion on, so it can't be used
    to launder an explicitly-excluded role shape via incidental JD phrasing.
    """
    if _title_hits_negative(title):
        return False
    if not _title_hits_positive(title):
        if not description_indicates_engagement_delivery_shape(description):
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


# Your geo criteria live in careeros/local_geo.py (gitignored -- copy from
# local_geo.example.py and fill in your own commute area). Kept out of this
# module so the repo never ships anyone's real location. geo_eligible() below
# uses TARGET_GEO_AREA for hybrid/onsite eligibility and OPEN_REMOTE_TERMS for
# remote roles that are open regardless of TARGET_GEO_AREA. Logic lives here
# (not in intake.py, where it originated) because scan.py needs it too (a
# role-keyword-only filter can't catch US-work-authorization-restricted or
# hard-onsite-elsewhere roles) and fetchers/base.py is the one shared module
# both scan.py and intake.py already import from without creating a circular
# import (intake.py imports _normalize_posted_at from scan.py, so scan.py
# importing from intake.py would cycle). This is the one copy; intake.py
# re-exports it.
try:
    from careeros.local_geo import OPEN_REMOTE_TERMS, TARGET_GEO_AREA
except ModuleNotFoundError:
    import warnings

    warnings.warn(
        "careeros/local_geo.py not found -- geo filtering will reject every "
        "hybrid/onsite role until you set it up. Copy careeros/local_geo.example.py "
        "to careeros/local_geo.py and fill in your own commute area.",
        stacklevel=2,
    )
    TARGET_GEO_AREA = []
    OPEN_REMOTE_TERMS = ["global", "worldwide", "anywhere"]

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
# restriction only showed up in the description body. Widened 2026-09-29:
# Glean ("must be located in US East,Central, or West timezone") used the
# bare "US" abbreviation instead of "United States", which the original
# "in the united states" phrasing didn't cover. Widened again 2026-10-14:
# Fivetran's "Partner Solutions Architect" posting had a location field
# reading "Remote, Any, United States, AMER" -- the "AMER" segment read as
# an open region per OPEN_REGION_SEGMENTS -- but the JD body said "This is
# a full-time position, which can be either remote (US) or hybrid
# depending on location", a parenthetical-qualifier phrasing none of the
# above patterns covered (same company/failure mode as 2026-09-04, just a
# different posting's wording). Deliberately narrow: only matches when the
# parenthetical contains US/USA alone, not a list like "remote (US,
# Canada)" which is genuinely open.
_US_ONLY_RESTRICTION_RE = re.compile(
    r"must\s+(?:be\s+)?(?:physically\s+)?(?:located|reside|residing)\s+in\s+(?:the\s+)?(?:united\s+states|u\.?s\.?)\b"
    r"|reside\s+within\s+the\s+[a-z]+\s+time\s*zone\s*\(\s*united\s+states\s*\)"
    r"|remote\s*\(\s*(?:the\s+)?u\.?s\.?a?\.?\s*\)",
    re.IGNORECASE,
)


def description_indicates_us_only(description: str) -> bool:
    """Return True if the JD body itself hard-restricts to US physical
    residency/timezone, regardless of what the location field implies."""
    if not description:
        return False
    return bool(_US_ONLY_RESTRICTION_RE.search(description))


# Greenhouse's own LinkedIn-integration job tags (#LI-Remote/#LI-Hybrid/
# #LI-Onsite), appended verbatim to the description body by the recruiter.
# Found empirically 2026-10-02: Fivetran's "Staff Solutions Engineer"
# carried remote_type="remote" and a location field reading "Remote, New
# York, United States, AMER" -- both of which look open -- but the JD body
# said "This is a full-time position based in New York City" and closed
# with "#LI-HYBRID", i.e. actually hybrid-onsite in a specific city. A tag
# mismatch like this can't be caught by description_indicates_us_only (it's
# not a residency restriction, just a mislabeled remote_type), so it needs
# its own check. High precision: this is a deliberate ATS convention, not
# incidental text, so a match is trustworthy on its own.
_LI_HYBRID_OR_ONSITE_TAG_RE = re.compile(r"#li-(?:hybrid|onsite|on-site)", re.IGNORECASE)


def description_indicates_hybrid_or_onsite_tag(description: str) -> bool:
    """Return True if the JD body carries a #LI-Hybrid/#LI-Onsite tag,
    overriding a "remote" remote_type/location field that doesn't say so."""
    if not description:
        return False
    return bool(_LI_HYBRID_OR_ONSITE_TAG_RE.search(description))


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
    per-role guarantee -- see description_indicates_us_only. Same for a
    #LI-Hybrid/#LI-Onsite tag contradicting a "remote" remote_type -- see
    description_indicates_hybrid_or_onsite_tag.
    """
    if description_indicates_us_only(description):
        return False
    loc = (location or "").lower().strip()
    loc_segments = [s.strip() for s in loc.split(",")]
    title_l = (title or "").lower()
    if any(t in title_l for t in TARGET_GEO_AREA):
        return True
    if remote_type == "remote" and description_indicates_hybrid_or_onsite_tag(description):
        remote_type = "hybrid"
    if remote_type in ("hybrid", "onsite"):
        return any(t in loc for t in TARGET_GEO_AREA)
    if remote_type == "remote":
        if any(t in loc for t in OPEN_REMOTE_TERMS) or any(t in loc for t in TARGET_GEO_AREA):
            return True
        if any(seg in OPEN_REGION_SEGMENTS for seg in loc_segments):
            return True
        return loc in ("", "remote")
    return any(t in loc for t in TARGET_GEO_AREA)
