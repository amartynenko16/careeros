"""Deterministic fit score (0-100) for the Applications tracker.

Same philosophy as the rest of CareerOS (see CLAUDE.md decision #4): rules
over your own stored data, no LLM judgment call baked into the number. The
initial score is computed once from what's already known about a job
(Bank tech/tool match via coach.py, company tier, comp disclosure, geo
openness, warm-contact signal). After that, it's a live field -- meant to
move as interview feedback and progression come in, which isn't
deterministic (a good phone screen isn't a fact CareerOS can observe), so
that part is a manual update via jobs.set_score(), same pattern as Stage
and Comp.

Weights are named constants below specifically so they're easy to retune
without hunting through the scoring logic.
"""

from typing import Any

from careeros import coach, db

# --- component weights (sum to 100) -----------------------------------------

BANK_MATCH_MAX = 40          # Bank tech/tool overlap with the JD (coach.py)
STRONG_MATCH_POINTS = 6      # per Strong-tagged record matched, capped below
STRONG_MATCH_CAP = 28
WORKING_MATCH_POINTS = 3     # per Working-tagged record matched, capped below
WORKING_MATCH_CAP = 12

COMPANY_TIER_MAX = 20
TIER_POINTS = {1: 20, 2: 12, 3: 6}
TIER_DEFAULT = 6             # untiered / manually-added companies

COMP_TRANSPARENCY_MAX = 15
COMP_DISCLOSED_POINTS = 15
COMP_UNDISCLOSED_POINTS = 8

GEO_MAX = 15
GEO_OPEN_POINTS = 15         # genuinely open remote, or in your target area
GEO_REGION_TAG_POINTS = 10   # open via a broad ATS region tag (e.g. "AMER")
GEO_BASELINE_POINTS = 8      # passed geo_eligible() some other way

WARM_CONTACT_MAX = 10


def _bank_match_component(job_id: str) -> tuple[int, str]:
    report = coach.analyze(job_id)
    counts = {tag: len(recs) for tag, recs in report["matches"].items()}
    strong = min(counts.get("Strong", 0) * STRONG_MATCH_POINTS, STRONG_MATCH_CAP)
    working = min(counts.get("Working", 0) * WORKING_MATCH_POINTS, WORKING_MATCH_CAP)
    points = min(strong + working, BANK_MATCH_MAX)
    reason = f"{counts.get('Strong', 0)} Strong + {counts.get('Working', 0)} Working Bank records matched the JD"
    return points, reason


def _company_tier_component(company_slug: str) -> tuple[int, str]:
    with db.connect() as conn:
        row = conn.execute("SELECT tier, notes FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    tier = row["tier"] if row else None
    points = TIER_POINTS.get(tier, TIER_DEFAULT)
    reason = f"tier {tier}" if tier else "untiered company"
    return points, reason


def _comp_component(comp: str | None) -> tuple[int, str]:
    if comp and comp.strip().upper() != "N/A":
        return COMP_DISCLOSED_POINTS, "comp disclosed in JD"
    return COMP_UNDISCLOSED_POINTS, "comp not disclosed (neutral)"


def _geo_component(location: str, remote_type: str) -> tuple[int, str]:
    from careeros.fetchers.base import OPEN_REMOTE_TERMS, TARGET_GEO_AREA

    loc = (location or "").lower()
    if remote_type == "remote" and any(t in loc for t in OPEN_REMOTE_TERMS):
        return GEO_OPEN_POINTS, "genuinely open remote"
    if remote_type in ("onsite", "hybrid") and any(t in loc for t in TARGET_GEO_AREA):
        return GEO_OPEN_POINTS, "in target commute area"
    if any(seg.strip() in ("amer", "americas") for seg in loc.split(",")):
        return GEO_REGION_TAG_POINTS, "open via broad ATS region tag"
    return GEO_BASELINE_POINTS, "geo-eligible, no stronger signal"


def _warm_contact_component(company_slug: str) -> tuple[int, str]:
    with db.connect() as conn:
        row = conn.execute("SELECT notes FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    notes = (row["notes"] or "").lower() if row else ""
    if "warm contact" in notes or "warm referral" in notes:
        return WARM_CONTACT_MAX, "warm contact noted for this company"
    return 0, "no warm contact on file"


def compute_initial_score(job_id: str) -> tuple[int, dict[str, Any]]:
    """Compute the deterministic initial fit score for a job.

    Returns (score, breakdown) where breakdown is JSON-serializable and
    matches what jobs.score_breakdown_json is for. Does not write anything;
    call jobs.set_score(job_id, score, breakdown) to persist.
    """
    from careeros import jobs

    job = jobs.get(job_id)

    bank_pts, bank_reason = _bank_match_component(job_id)
    tier_pts, tier_reason = _company_tier_component(job["company_slug"])
    comp_pts, comp_reason = _comp_component(job.get("comp"))
    geo_pts, geo_reason = _geo_component(job.get("location", ""), job.get("remote_type", ""))
    warm_pts, warm_reason = _warm_contact_component(job["company_slug"])

    total = bank_pts + tier_pts + comp_pts + geo_pts + warm_pts

    breakdown = {
        "bank_match": {"points": bank_pts, "max": BANK_MATCH_MAX, "reason": bank_reason},
        "company_tier": {"points": tier_pts, "max": COMPANY_TIER_MAX, "reason": tier_reason},
        "comp_transparency": {"points": comp_pts, "max": COMP_TRANSPARENCY_MAX, "reason": comp_reason},
        "geo": {"points": geo_pts, "max": GEO_MAX, "reason": geo_reason},
        "warm_contact": {"points": warm_pts, "max": WARM_CONTACT_MAX, "reason": warm_reason},
        "basis": "initial",
    }
    return total, breakdown
