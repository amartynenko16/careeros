"""Phase 4, Model B2: tailoring package generator.

CareerOS stays deterministic and LLM-free at runtime (see CLAUDE.md decision
#4). This module does not rephrase anything -- it selects the raw material
(job description + every non-Never-Claim Bank record) and packages it with a
strict prompt. The actual tailoring -- rephrasing bullets toward the JD's
language while inventing nothing -- happens in a separate LLM call outside
this codebase (Claude Code or similar), constrained by that prompt.

Bank record selection is deliberately NOT narrowed to Coach's tech/tool
matches here: Coach only reasons about tech vocabulary, so for a role like a
TAM/TSM/Implementation position most of the actually-relevant bullets
(relationship management, onboarding, portfolio ownership) would never
surface. Instead every eligible record is included and selection is left to
the tailoring step, which has full JD context to judge relevance.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from careeros import db, jobs
from careeros.config import APPLICATIONS_DIR


NEVER_CLAIM = "Never Claim"

# Hard cap on the COMBINED length of every bullet approved for one resume
# (changed and kept-as-is alike), chosen to keep the whole thing to one
# page. Not a per-bullet limit -- individual bullets can run long or short
# as long as the total stays under budget. Not a Bank/JD property; adjust
# here if the target resume layout changes.
TOTAL_BULLET_CHAR_BUDGET = 2950

NO_FABRICATION_PROMPT = f"""\
You are tailoring resume bullets for one specific job application.

Rules (non-negotiable):
1. Do not invent metrics, tools, technologies, scope, or outcomes that are
   not already stated in the bullet, its Notes, or its Metrics field below.
2. You MAY: reorder, re-emphasize, tighten, and rephrase language to mirror
   terminology the job description uses, as long as the underlying claim
   stays true to the source record.
3. You MAY select a subset of the records below (not all will be relevant);
   prioritize the ones most relevant to this job description.
4. If a bullet would need a fabricated detail to sound relevant, leave it
   out or flag it rather than inventing the detail.
5. Records marked "Gap" or "Untagged" honesty tag should be phrased more
   conservatively than "Strong" ones; don't overstate confidence the
   underlying record doesn't support.
6. There is no fixed length for an individual bullet. Instead, the combined
   length of every bullet you select for this job's resume -- across every
   role/employer that will appear, not just the highest-relevance ones --
   must not exceed {TOTAL_BULLET_CHAR_BUDGET} characters total (spaces
   included). Give your strongest, most JD-relevant stories more room; keep
   lower-priority bullets short. State your running total as you go and the
   final total at the end, but treat that as a draft estimate only -- you
   will run `careeros tailor budget` to verify the real count before this
   is final.
7. No em dashes anywhere in the output. Use commas, semicolons, colons, or
   split sentences instead.
8. Output: for each selected record, the original Standard bullet followed
   by your tailored rewrite, so the human reviewer can compare
   before/after and catch anything that drifted from the source.

Never claim records have already been excluded from this package; do not
reintroduce them even if you recall them from elsewhere.
"""


def _effective_honesty(rec: dict) -> str | None:
    return rec.get("local_honesty_tag") or rec.get("honesty_tag") or None


def _tech_terms(rec: dict) -> list[str]:
    raw = rec.get("tech_tools_json")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def _employers(rec: dict) -> list[str]:
    raw = rec.get("employers_json")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def eligible_records() -> list[dict[str, Any]]:
    """Every Bank record except those tagged Never Claim (local override wins)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM bank_records").fetchall()
    records = [dict(r) for r in rows]
    return [r for r in records if _effective_honesty(r) != NEVER_CLAIM]


def build_package(job_id: str) -> str:
    """Assemble the Markdown tailoring package for one job."""
    job = jobs.get(job_id)
    records = eligible_records()

    lines: list[str] = []
    lines.append(f"# Tailoring package: {job['title']} @ {job['company_slug']}")
    lines.append("")
    lines.append(f"Job ID: {job['id']}")
    lines.append(f"Location: {job['location'] or '-'}    Remote: {job['remote_type'] or '-'}")
    lines.append(f"URL: {job['ats_url'] or '-'}")
    lines.append("")
    lines.append("## No-fabrication instructions")
    lines.append("")
    lines.append(NO_FABRICATION_PROMPT)
    lines.append("")
    lines.append("## Job description")
    lines.append("")
    lines.append(job["description"] or "(no description fetched)")
    lines.append("")
    lines.append(f"## Bank records ({len(records)} eligible, Never Claim excluded)")
    lines.append("")

    for rec in records:
        tag = _effective_honesty(rec) or "Untagged"
        employers = ", ".join(_employers(rec)) or "-"
        tools = ", ".join(_tech_terms(rec)) or "-"
        lines.append(f"### {rec['name']}")
        lines.append(f"- Grain: {rec.get('grain') or '-'}")
        lines.append(f"- Employer(s): {employers}")
        lines.append(f"- Timeframe: {rec.get('timeframe') or '-'}")
        lines.append(f"- Honesty tag: {tag}")
        lines.append(f"- Tech/tools: {tools}")
        lines.append(f"- Bullet (Standard): {rec.get('bullet_standard') or '-'}")
        if rec.get("metrics"):
            lines.append(f"- Metrics: {rec['metrics']}")
        if rec.get("notes"):
            lines.append(f"- Notes: {rec['notes']}")
        lines.append("")

    return "\n".join(lines)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def write_package(job_id: str) -> str:
    """Build the package, write it to applications/<slug>/, and record it.

    Returns the path to the written file.
    """
    job = jobs.get(job_id)
    content = build_package(job_id)

    folder_slug = f"{job['company_slug']}-{_slugify(job['title'])}"
    folder = APPLICATIONS_DIR / folder_slug
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / "tailoring_package.md"
    out_path.write_text(content, encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO applications (id, job_id, folder_path, created_at) "
            "VALUES (?, ?, ?, ?)",
            (job_id, job_id, str(folder), now),
        )
        conn.commit()

    return str(out_path)


def check_budget(path: str) -> dict[str, Any]:
    """Deterministically total the character count of an approved-bullets file.

    This is the real check -- the LLM tailoring step's self-reported running
    total in the package prompt is a draft estimate, not authoritative.
    Expects one bullet per line (plain text, or "- "/"-" prefixed Markdown
    list items). Blank lines and lines starting with "#" (headings/comments)
    are ignored, so you can paste the "Tailored" lines straight out of a
    tailored_bullets.md review file.
    """
    text = Path(path).read_text(encoding="utf-8")
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        elif stripped.startswith("-"):
            stripped = stripped[1:].strip()
        bullets.append(stripped)

    total = sum(len(b) for b in bullets)
    return {
        "bullets": bullets,
        "count": len(bullets),
        "total_chars": total,
        "budget": TOTAL_BULLET_CHAR_BUDGET,
        "remaining": TOTAL_BULLET_CHAR_BUDGET - total,
        "over_budget": total > TOTAL_BULLET_CHAR_BUDGET,
    }
