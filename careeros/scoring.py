"""Live Fit Score (0-100) for the Applications tracker.

Same philosophy as the rest of CareerOS (see CLAUDE.md decision #4): rules
over your own stored data, no LLM judgment call baked into the number. The
score blends four things that together answer "where should my time go":

  1. Bank/role match     -- do you actually have the experience for this?
  2. Comp attractiveness -- is it worth it, against your own floor?
  3. Remote preference    -- flexibility, independent of geo eligibility
     (geo_eligible() already filtered out anything you couldn't take)
  4. Landing likelihood  -- real momentum: interview stage progress, plus
     a warm-contact bonus

Company tier isn't a separate component here (a tier is "should I bother
applying," which is already decided by the time a job has a score at all
-- scoring an application shouldn't re-litigate the target list).

compute_initial_score() is the deterministic baseline. It's a starting
point, not the final word: interview context (a hiring manager's tone, a
warm contact's read on the room, a recruiter going quiet) isn't something
CareerOS can observe, so layer that in by hand via jobs.set_score() with
your own adjusted number and a short describe_score()-style note, the same
way describe_score() would phrase it. Weights are named constants
specifically so they're easy to retune.
"""

import re
from typing import Any

from careeros import coach, db

try:
    from careeros.local_criteria import COMP_FLOOR
except ModuleNotFoundError:
    import warnings

    warnings.warn(
        "careeros/local_criteria.py not found -- comp-attractiveness scoring "
        "will treat every disclosed figure as neutral. Copy "
        "careeros/local_criteria.example.py to careeros/local_criteria.py "
        "and fill in your own comp floor.",
        stacklevel=2,
    )
    COMP_FLOOR = None

# --- component weights (sum to 100) -----------------------------------------

BANK_MATCH_MAX = 20
STRONG_MATCH_POINTS = 3
STRONG_MATCH_CAP = 14
WORKING_MATCH_POINTS = 2
WORKING_MATCH_CAP = 6

COMP_MAX = 20
COMP_WELL_ABOVE_FLOOR = 20    # >= floor + ~15%
COMP_AT_FLOOR = 15            # >= floor
COMP_BELOW_FLOOR_CLOSE = 8    # within ~15% under floor
COMP_WELL_BELOW_FLOOR = 3
COMP_UNKNOWN = 10             # undisclosed / unconfirmed -- neutral, not penalized

REMOTE_MAX = 20
REMOTE_POINTS = {"remote": 20, "hybrid": 5, "onsite": 0}
REMOTE_DEFAULT = 0

LANDING_MAX = 40
STAGE_LIKELIHOOD = {
    None: 10,
    "": 10,
    "1. Phone Screen": 15,
    "2. First Round Interview": 20,
    "2.5. Follow-Up Email": 18,
    "3. Second Round Interview": 27,
    "3.5. Follow-Up Email": 24,
    "4. Final Round": 33,
    "4.5. Follow-Up Email": 30,
    "5. Offer": 40,
    "Rejected by Company": 0,
    "Withdrawn": 0,
}
WARM_CONTACT_BONUS = 5

# A number, optionally a range, in $ or bare, followed by "K" (case-insensitive).
_COMP_FIGURE_RE = re.compile(r"\$?\s*([\d,]+)\s*(?:-\s*\$?\s*([\d,]+))?\s*K", re.IGNORECASE)


def _bank_match_component(job_id: str) -> tuple[int, str]:
    report = coach.analyze(job_id)
    counts = {tag: len(recs) for tag, recs in report["matches"].items()}
    strong = min(counts.get("Strong", 0) * STRONG_MATCH_POINTS, STRONG_MATCH_CAP)
    working = min(counts.get("Working", 0) * WORKING_MATCH_POINTS, WORKING_MATCH_CAP)
    points = min(strong + working, BANK_MATCH_MAX)
    if points >= BANK_MATCH_MAX * 0.7:
        label = "strong Bank match"
    elif points >= BANK_MATCH_MAX * 0.3:
        label = "partial Bank match"
    else:
        label = "weak Bank match"
    return points, label


def _base_figure_from_segment(segment: str) -> int | None:
    if "unconfirmed" in segment.lower():
        return None
    # Only the part before any parenthetical (OTE/bonus), so a base range
    # isn't confused with a higher OTE range in the same segment.
    base_part = re.split(r"\(", segment)[0]
    fig = _COMP_FIGURE_RE.search(base_part)
    if not fig:
        return None
    high = fig.group(2) or fig.group(1)
    return int(high.replace(",", ""))


def _highest_base_figure_k(comp: str) -> int | None:
    """Pull the highest base-salary figure (in thousands) out of a Comp
    string, preferring a landed figure over a posted one since it's the
    more concrete number. Ignores OTE/bonus figures and anything marked
    unconfirmed. Returns None if nothing parseable.

    Format: "<landed> - Posted: <posted>" when both are known (the landed
    figure leads, no label), or just "Posted: <posted>" when there's no
    landed figure yet. Older "Posted: ... / Landed: ..." text (labeled,
    posted-first) is also still accepted."""
    if not comp or comp.strip().upper() == "N/A":
        return None

    # Current format: landed figure (if any) leads, unlabeled, before " - Posted:".
    m = re.split(r"\s*-\s*Posted:", comp, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2:
        landed_part, posted_part = m
        fig = _base_figure_from_segment(landed_part)
        if fig is not None:
            return fig
        return _base_figure_from_segment(posted_part)

    # Older labeled format, or posted-only with no landed figure at all.
    for label in ("Landed", "Posted", "Post"):
        lm = re.search(rf"{label}:\s*(.*?)(?:/|$)", comp, re.IGNORECASE)
        if not lm:
            continue
        fig = _base_figure_from_segment(lm.group(1))
        if fig is not None:
            return fig
    return None


def _comp_component(comp: str | None) -> tuple[int, str]:
    if COMP_FLOOR is None:
        return COMP_UNKNOWN, "comp floor not configured"

    figure_k = _highest_base_figure_k(comp or "")
    if figure_k is None:
        return COMP_UNKNOWN, "comp undisclosed/unconfirmed"

    floor_k = COMP_FLOOR / 1000
    if figure_k >= floor_k * 1.15:
        return COMP_WELL_ABOVE_FLOOR, f"${figure_k:.0f}K well above floor"
    if figure_k >= floor_k:
        return COMP_AT_FLOOR, f"${figure_k:.0f}K at/above floor"
    if figure_k >= floor_k * 0.85:
        return COMP_BELOW_FLOOR_CLOSE, f"${figure_k:.0f}K close to floor"
    return COMP_WELL_BELOW_FLOOR, f"${figure_k:.0f}K well below floor"


def _remote_component(remote_type: str) -> tuple[int, str]:
    points = REMOTE_POINTS.get(remote_type, REMOTE_DEFAULT)
    label = {"remote": "fully remote", "hybrid": "hybrid", "onsite": "on-site"}.get(remote_type, remote_type or "unknown")
    return points, label


def _warm_contact(company_slug: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT notes FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    notes = (row["notes"] or "").lower() if row else ""
    return "warm contact" in notes or "warm referral" in notes


def _landing_component(application_stage: str | None, company_slug: str) -> tuple[int, str]:
    base = STAGE_LIKELIHOOD.get(application_stage, STAGE_LIKELIHOOD[None])
    warm = _warm_contact(company_slug)
    points = min(base + (WARM_CONTACT_BONUS if warm else 0), LANDING_MAX)

    if application_stage in ("Rejected by Company", "Withdrawn"):
        label = "closed out"
    elif not application_stage:
        label = "no interview movement yet"
    else:
        label = f"at {application_stage}"
    if warm:
        label += ", warm contact"
    return points, label


def compute_initial_score(job_id: str) -> tuple[int, dict[str, Any]]:
    """Compute the deterministic fit score for a job right now. Safe to
    re-run any time application_stage or comp changes -- it always reflects
    current state, not just the first computation. Returns (score,
    breakdown); call jobs.set_score(job_id, score, breakdown) to persist,
    and describe_score(breakdown) for the short text to put in Notes."""
    from careeros import jobs

    job = jobs.get(job_id)

    bank_pts, bank_label = _bank_match_component(job_id)
    comp_pts, comp_label = _comp_component(job.get("comp"))
    remote_pts, remote_label = _remote_component(job.get("remote_type", ""))
    landing_pts, landing_label = _landing_component(job.get("application_stage"), job["company_slug"])

    total = bank_pts + comp_pts + remote_pts + landing_pts

    breakdown = {
        "bank_match": {"points": bank_pts, "max": BANK_MATCH_MAX, "label": bank_label},
        "comp": {"points": comp_pts, "max": COMP_MAX, "label": comp_label},
        "remote": {"points": remote_pts, "max": REMOTE_MAX, "label": remote_label},
        "landing": {"points": landing_pts, "max": LANDING_MAX, "label": landing_label},
        "basis": "computed",
    }
    return total, breakdown


def describe_score(breakdown: dict[str, Any], extra: str = "") -> str:
    """Render a breakdown into a short Notes-ready sentence (aim for well
    under NOTES_MAX_CHARS=200). `extra` is for hand-added interview context
    a formula can't see (e.g. "HM very enthusiastic") -- append it after
    the computed parts."""
    parts = [
        breakdown["bank_match"]["label"],
        breakdown["comp"]["label"],
        breakdown["remote"]["label"],
        breakdown["landing"]["label"],
    ]
    text = "; ".join(parts).capitalize() + "."
    if extra:
        text = f"{text} {extra}"
    return text
