"""Live Fit Score (0-100, up to 105 with a warm-contact bonus) for the
Applications tracker.

Same philosophy as the rest of CareerOS (see CLAUDE.md decision #4): rules
over your own stored data, no LLM judgment call baked into the number. The
score blends three things that together answer "is this actually a good
fit for me":

  1. Bank/role match     -- do you actually have the experience for this?
  2. Comp attractiveness -- is it worth it, against your own floor?
  3. Remote preference    -- flexibility, independent of geo eligibility
     (geo_eligible() already filtered out anything you couldn't take)

Plus a flat warm-contact bonus (a personal connection at the company),
independent of the above.

Plus, since 2026-09-22, a manual "desire" adjustment (-50 to +50, via
jobs.set_desire()) for the one thing none of the above can see: do you
actually want it? Culture signals, product pull, concrete friction, cases
for instability. Unrated is neutral (0), not indifferent -- most jobs won't
have this set, and describe_score() omits the label entirely until they do.
This is the intended fix for a case like Innosphere: a role that clears
Bank/comp/remote cleanly but that you've decided, after real signal, to
treat as a fallback rather than a priority. Range widened from an initial
-15/+10 to a symmetric -50/+50 on 2026-09-22 -- wide enough that a strongly
felt desire can dominate the other four components outright, not just nudge
them.

**Stage/interview progress deliberately does NOT factor in (changed
2026-09-17).** How far along you are in a process is a momentum signal,
not a fit signal -- a job you're at the final round for isn't a better
fit than one you just applied to, and scoring it that way conflated "where
should my time go right now" with "is this actually good for me." The
component previously here (LANDING_MAX=40, keyed off application_stage)
was removed outright rather than zeroed, and its 40 points were
redistributed into Bank/Comp/Remote (see their MAX constants below) so the
score still tops out at 100. Stage is still tracked (`jobs stage`, the
Notion Stage column) for pipeline visibility -- it just isn't scored.

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

# --- component weights (Bank + Comp + Remote sum to 100; warm bonus is extra,
# redistributed 2026-09-17 from the removed 40-pt stage/landing component --
# see module docstring) ------------------------------------------------------

BANK_MATCH_MAX = 32
STRONG_MATCH_POINTS = 5
STRONG_MATCH_CAP = 22
WORKING_MATCH_POINTS = 3
WORKING_MATCH_CAP = 10

COMP_MAX = 32
COMP_WELL_ABOVE_FLOOR = 32    # >= floor + ~15%
COMP_AT_FLOOR = 24            # >= floor
COMP_BELOW_FLOOR_CLOSE = 13   # within ~15% under floor
COMP_WELL_BELOW_FLOOR = 5
COMP_UNKNOWN = 16             # undisclosed / unconfirmed -- neutral, not penalized

REMOTE_MAX = 31
REMOTE_POINTS = {"remote": 31, "hybrid": 8, "onsite": 0}
REMOTE_DEFAULT = 0

WARM_CONTACT_BONUS = 5  # flat bonus, independent of stage since 2026-09-17

# Manual "do you actually want this" signal (2026-09-22) -- culture, product
# pull, friction, instability, none of which a formula can see.
# jobs.set_desire() takes a -50/+50 input so you have room to express real
# intensity, but that value does NOT land 1:1 in fit-score points -- tried
# that first, and it immediately clamped several jobs to a flat 100 or 0,
# erasing exactly the ranking distinctions desire was supposed to add.
# DESIRE_WEIGHT scales the input down to a max +/-15 swing on the score
# itself (same day, same fix) -- enough to meaningfully move a ranking,
# not enough to single-handedly saturate it. Read straight from
# jobs.desire_score -- unset/0 is neutral (no adjustment), not "indifferent."
DESIRE_MIN = -50
DESIRE_MAX = 50
DESIRE_WEIGHT = 0.3

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
    # Only the part before any parenthetical (OTE/bonus), so a base range
    # isn't confused with a higher OTE range in the same segment -- and so
    # an "(OTE unconfirmed)" aside doesn't disqualify an otherwise-confirmed
    # base figure (found 2026-10-07, Schema App: "$120K base (OTE
    # unconfirmed)" was scoring as comp-unknown).
    base_part = re.split(r"\(", segment)[0]
    if "unconfirmed" in base_part.lower():
        return None
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

    # No "Posted:"/"Landed:" label at all -- a confirmed figure typed
    # directly with no convention prefix (found 2026-09-22, Supabase HM-stage
    # comp: "$140-160K base ($175-200K OTE)"). Treat the whole string as a
    # bare landed figure; still returns None if it's marked unconfirmed.
    return _base_figure_from_segment(comp)


def _comp_component(comp: str | None) -> tuple[int, str]:
    """COMP_WELL_BELOW_FLOOR/COMP_AT_FLOOR/COMP_WELL_ABOVE_FLOOR are anchor
    points at 85%/100%/115% of floor; points between anchors are linearly
    interpolated rather than stepped, so a small gap near the floor (e.g.
    $118K vs a $120K floor -- 2%) produces a small point difference instead
    of a cliff (changed 2026-09-17, after GitLab's $118K scored barely half
    of AfterShip's $120K under the old fixed-band version for a ~2% comp
    gap). Labels still describe which band the figure falls in, for
    readability; only the underlying points became continuous."""
    if COMP_FLOOR is None:
        return COMP_UNKNOWN, "comp floor not configured"

    figure_k = _highest_base_figure_k(comp or "")
    if figure_k is None:
        return COMP_UNKNOWN, "comp undisclosed/unconfirmed"

    floor_k = COMP_FLOOR / 1000
    ratio = figure_k / floor_k

    if ratio >= 1.15:
        points = COMP_WELL_ABOVE_FLOOR
    elif ratio <= 0.85:
        points = COMP_WELL_BELOW_FLOOR
    elif ratio >= 1.0:
        frac = (ratio - 1.0) / 0.15
        points = COMP_AT_FLOOR + frac * (COMP_WELL_ABOVE_FLOOR - COMP_AT_FLOOR)
    else:
        frac = (ratio - 0.85) / 0.15
        points = COMP_WELL_BELOW_FLOOR + frac * (COMP_AT_FLOOR - COMP_WELL_BELOW_FLOOR)
    points = round(points)

    if ratio >= 1.15:
        label = f"${figure_k:.0f}K well above floor"
    elif ratio >= 1.0:
        label = f"${figure_k:.0f}K at/above floor"
    elif ratio >= 0.85:
        label = f"${figure_k:.0f}K close to floor"
    else:
        label = f"${figure_k:.0f}K well below floor"
    return points, label


def _remote_component(remote_type: str) -> tuple[int, str]:
    points = REMOTE_POINTS.get(remote_type, REMOTE_DEFAULT)
    label = {"remote": "fully remote", "hybrid": "hybrid", "onsite": "on-site"}.get(remote_type, remote_type or "unknown")
    return points, label


def _warm_contact(company_slug: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT notes FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    notes = (row["notes"] or "").lower() if row else ""
    return "warm contact" in notes or "warm referral" in notes


def _warm_contact_component(company_slug: str) -> tuple[int, str]:
    warm = _warm_contact(company_slug)
    points = WARM_CONTACT_BONUS if warm else 0
    label = "warm contact" if warm else "no warm contact"
    return points, label


def _desire_component(job: dict[str, Any]) -> tuple[int, str, bool, int]:
    """Reads jobs.desire_score directly -- this is the one component that's
    entirely a manual judgment call, not derived from JD/comp/remote data.
    The stored value (-50/+50) is your own expressiveness scale; DESIRE_WEIGHT
    scales it down to what actually lands on the score, so a strongly-felt
    rating nudges the ranking instead of flattening it against 0 or 100.
    Returns (points, label, rated, raw) so describe_score() can skip the
    label for unrated jobs, and callers can still see the original number."""
    raw = job.get("desire_score")
    if raw is None:
        return 0, "not rated", False, 0
    raw = max(DESIRE_MIN, min(DESIRE_MAX, int(raw)))
    points = round(raw * DESIRE_WEIGHT)
    if points > 0:
        label = f"+{points} (rated {raw:+d}) wanted it"
    elif points < 0:
        label = f"{points} (rated {raw:+d}) didn't want it"
    else:
        label = f"neutral on wanting it (rated {raw:+d})"
    return points, label, True, raw


def compute_initial_score(job_id: str) -> tuple[int, dict[str, Any]]:
    """Compute the deterministic fit score for a job right now. Safe to
    re-run any time comp changes -- it always reflects current state, not
    just the first computation. Returns (score, breakdown); call
    jobs.set_score(job_id, score, breakdown) to persist, and
    describe_score(breakdown) for the short text to put in Notes."""
    from careeros import jobs

    job = jobs.get(job_id)

    bank_pts, bank_label = _bank_match_component(job_id)
    comp_pts, comp_label = _comp_component(job.get("comp"))
    remote_pts, remote_label = _remote_component(job.get("remote_type", ""))
    warm_pts, warm_label = _warm_contact_component(job["company_slug"])
    desire_pts, desire_label, desire_rated, desire_raw = _desire_component(job)

    total = max(0, min(100, bank_pts + comp_pts + remote_pts + warm_pts + desire_pts))

    breakdown = {
        "bank_match": {"points": bank_pts, "max": BANK_MATCH_MAX, "label": bank_label},
        "comp": {"points": comp_pts, "max": COMP_MAX, "label": comp_label},
        "remote": {"points": remote_pts, "max": REMOTE_MAX, "label": remote_label},
        "warm_contact": {"points": warm_pts, "max": WARM_CONTACT_BONUS, "label": warm_label},
        "desire": {
            "points": desire_pts,
            "max": round(DESIRE_MAX * DESIRE_WEIGHT), "min": round(DESIRE_MIN * DESIRE_WEIGHT),
            "raw": desire_raw, "raw_min": DESIRE_MIN, "raw_max": DESIRE_MAX,
            "label": desire_label, "rated": desire_rated,
        },
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
        breakdown["warm_contact"]["label"],
    ]
    if breakdown.get("desire", {}).get("rated"):
        parts.append(breakdown["desire"]["label"])
    text = "; ".join(parts).capitalize() + "."
    if extra:
        text = f"{text} {extra}"
    return text
