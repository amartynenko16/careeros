"""Coach: deterministic gap analysis of Bank records against a job.

Matches a job's fetched description text against each Bank record's tech/tool
vocabulary (`tech_tools_json`), grouped by effective Honesty Tag (local
override first, then the Notion value). Records tagged Never Claim are
excluded entirely -- they must never surface in any job-facing output.

Deliberately narrow: this only reasons about tech/tool terms that already
exist somewhere in your own Bank. It will not tell you a JD wants
"Kubernetes" unless some Bank record already lists Kubernetes among its
tools. An external skills dictionary could flag things that were never
actually relevant to your experience, which cuts against the no-fabrication
rule this project runs on -- so there isn't one. Judgment on what a gap
actually means is left to you.
"""

import json
from typing import Any

from careeros import db, jobs


NEVER_CLAIM = "Never Claim"

# Display order for grouping matched records. Untagged records (no honesty
# tag set at all) sort last, after Gap.
HONESTY_DISPLAY_ORDER = ["Strong", "Working", "Gap", "Untagged"]


def _effective_honesty(rec: dict) -> str | None:
    """Local vetting overrides the Notion value; Notion value is the fallback."""
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


def analyze(job_id: str) -> dict[str, Any]:
    """Match one job's description against the Bank's tech/tool vocabulary.

    Returns:
        job: the job row.
        matches: {honesty_tag: [{"record": ..., "matched_terms": [...]}]}.
        unmatched_bank_tools: Bank tools (from non-Never-Claim records) that
            don't appear anywhere in this job's description text.
    """
    job = jobs.get(job_id)
    description = (job.get("description") or "").lower()

    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM bank_records").fetchall()
    records = [dict(r) for r in rows]

    matches: dict[str, list[dict]] = {}
    all_bank_terms: set[str] = set()
    matched_terms_overall: set[str] = set()

    for rec in records:
        tag = _effective_honesty(rec)
        if tag == NEVER_CLAIM:
            continue

        terms = _tech_terms(rec)
        all_bank_terms.update(terms)

        matched = [t for t in terms if t.lower() in description]
        if matched:
            matched_terms_overall.update(matched)
            matches.setdefault(tag or "Untagged", []).append(
                {"record": rec, "matched_terms": matched}
            )

    unmatched_bank_tools = sorted(
        all_bank_terms - matched_terms_overall, key=str.lower
    )

    return {
        "job": job,
        "matches": matches,
        "unmatched_bank_tools": unmatched_bank_tools,
    }


def compare(status: str = "saved", limit: int = 200) -> list[dict[str, Any]]:
    """Summarize match strength per job for jobs in the given status.

    Sorted best-fit first: most Strong-tagged matches, then most total
    matched records.
    """
    rows = jobs.list_jobs(status=status, limit=limit)
    results = []
    for job in rows:
        report = analyze(job["id"])
        counts = {tag: len(recs) for tag, recs in report["matches"].items()}
        results.append(
            {
                "job": job,
                "counts": counts,
                "total_matched_records": sum(counts.values()),
            }
        )

    results.sort(
        key=lambda r: (r["counts"].get("Strong", 0), r["total_matched_records"]),
        reverse=True,
    )
    return results
