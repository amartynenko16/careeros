"""Push CareerOS job/application status to the Notion 📋 Applications database.

One-way, the opposite direction from notion_sync.py: local SQLite is the
source of truth for job/application state (that's where `careeros jobs
save/reject/mark-applied/stage` write), this only pushes outward so you can
see status in Notion without it becoming a second place edits have to
happen. Editing a row in Notion does nothing locally; there is no pull path
back, deliberately, to avoid two sources of truth for the same state.

Scope: only pushes jobs with status in ('saved', 'applied') by default --
the ones with active relevance. The 250+ auto-rejected jobs from role/geo
filtering would just be clutter in Notion and aren't what "don't lose track
of my applications" was about.

Upsert is keyed on jobs.notion_applications_page_id, set locally on first
push. If that's somehow cleared, a name+CareerOS-Job-ID lookup is used as a
fallback rather than blindly creating a duplicate row.

**Existing rows are never written to on a push, by default.** A row that
already has a Notion page is left completely alone -- your workspace is the
only thing that edits it from then on. Only
jobs with no existing page get a new row created. This replaced an earlier
design where Job URL/Location/Date Applied/Last Synced refreshed on every
push; that still risked clobbering something you had touched by hand, which
Company/Name/Remote Type/Stage were already carved out from (2026-09-02) for
the same reason. Pass update_existing=True (CLI: --update-existing) to
explicitly opt into refreshing an existing row's bookkeeping fields (Job
URL, Location, Date Applied, Last Synced only -- Name/Company/Stage/Remote
Type still never get touched post-creation); only do this when you ask
for it directly, not as a default behavior.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from notion_client import Client

from careeros import db
from careeros.config import NOTION_APPLICATIONS_DATA_SOURCE_ID, NOTION_TOKEN
from careeros.resume import _company_display_name


PROP_NAME = "Name"
PROP_COMPANY = "Company"
PROP_JOB_ID = "CareerOS Job ID"
PROP_STAGE = "Stage"
PROP_URL = "Job URL"
PROP_LOCATION = "Location"
PROP_REMOTE_TYPE = "Remote Type"
PROP_COMP = "Comp"
PROP_FIT_SCORE = "Fit Score"
PROP_NOTES = "Notes"
PROP_DATE_APPLIED = "Date Applied"
PROP_LAST_SYNCED = "Last Synced"

# Status is NOT written here: treat it as a Notion formula (reads
# off Stage, defaults to "Applied" when Stage is empty, confirmed 2026-09-02)
# after this database was created. It's read-only from the API's perspective;
# writing to it is silently ignored, so don't bother building a value for it.
_REMOTE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}


class NotionAppsConfigError(RuntimeError):
    pass


def _require_config() -> tuple[str, str]:
    if not NOTION_TOKEN:
        raise NotionAppsConfigError("NOTION_TOKEN is not set. See .env.example.")
    if not NOTION_APPLICATIONS_DATA_SOURCE_ID:
        raise NotionAppsConfigError("NOTION_APPLICATIONS_DATA_SOURCE_ID is not set. See .env.example.")
    return NOTION_TOKEN, NOTION_APPLICATIONS_DATA_SOURCE_ID


@dataclass
class PushSummary:
    considered: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value or ""}}]}


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value or "(untitled)"}}]}


def _select(value: str | None) -> dict[str, Any]:
    return {"select": ({"name": value} if value else None)}


def _url(value: str) -> dict[str, Any]:
    return {"url": value or None}


def _date(value: str | None) -> dict[str, Any]:
    return {"date": ({"start": value} if value else None)}


def _number(value: int | None) -> dict[str, Any]:
    return {"number": value}


def _notion_display_title(title: str) -> str:
    """Naming convention for the Notion Name column:
    just the role title, no company suffix (Company is its own column now),
    with "Senior" shortened to "Sr." Local jobs.title stays the original
    full title unchanged; this is a display-only transform for Notion."""
    return re.sub(r"\bSenior\b", "Sr.", title.strip())


# Set once, on page creation, then left alone -- you edit these directly
# in Notion and a later push must not clobber that. Status isn't here at all;
# it's a formula Notion computes from Stage.
def _initial_properties(job: dict[str, Any]) -> dict[str, Any]:
    return {
        PROP_NAME: _title(_notion_display_title(job["title"])),
        PROP_COMPANY: _rich_text(_company_display_name(job["company_slug"])),
        PROP_STAGE: _select(job.get("application_stage") or None),
        PROP_REMOTE_TYPE: _select(_REMOTE_LABELS.get(job.get("remote_type", ""), "Unknown")),
        PROP_COMP: _rich_text(job.get("comp") or ""),
        PROP_FIT_SCORE: _number(job.get("score")),
        PROP_NOTES: _rich_text(job.get("notes") or ""),
    }


# Bookkeeping fields refreshed on every push, existing page or not.
def _synced_properties(job: dict[str, Any], now_iso: str) -> dict[str, Any]:
    return {
        PROP_JOB_ID: _rich_text(job["id"]),
        PROP_URL: _url(job.get("ats_url", "")),
        PROP_LOCATION: _rich_text(job.get("location", "")),
        PROP_DATE_APPLIED: _date(job.get("date_applied")),
        PROP_LAST_SYNCED: _date(now_iso),
    }


@dataclass
class PullSummary:
    considered: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)


def _select_value(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    sel = prop.get("select")
    return sel["name"] if sel else None


def _rich_text_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("rich_text") or [])


def pull_editable_fields() -> PullSummary:
    """Pull Stage and Comp from Notion into local SQLite -- the two fields
    you hand-edit directly in the Applications table. This is the one
    deliberate exception to push()'s one-way design: the only path where
    Notion overrides local state, and only for these two fields (never
    Name/Company/Remote Type/anything else -- those still flow local ->
    Notion only). Run this before recomputing Fit Score, or scoring works
    off stale local data.

    A blank value in Notion never overwrites a non-blank local value --
    only an actual edit (a real Stage selection, real Comp text) pulls.
    """
    from careeros import jobs as jobs_module

    token, data_source_id = _require_config()
    client = Client(auth=token)
    summary = PullSummary()

    cursor: str | None = None
    while True:
        resp = client.data_sources.query(
            data_source_id=data_source_id,
            start_cursor=cursor,
            page_size=100,
        )
        for page in resp.get("results", []):
            summary.considered += 1
            props = page.get("properties", {})
            job_id = _rich_text_value(props.get(PROP_JOB_ID))
            if not job_id:
                continue  # a manually-added Notion-only row with no local job to pull into

            with db.connect() as conn:
                existing = conn.execute(
                    "SELECT application_stage, comp FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            if existing is None:
                summary.skipped.append(f"{job_id}: no matching local job")
                continue

            stage = _select_value(props.get(PROP_STAGE))
            comp = _rich_text_value(props.get(PROP_COMP)).strip()

            changed = False
            if stage and stage != existing["application_stage"]:
                if stage not in jobs_module.VALID_STAGES:
                    summary.skipped.append(f"{job_id}: unrecognized Stage {stage!r}")
                    stage = None
                else:
                    changed = True
            if comp and comp != (existing["comp"] or ""):
                changed = True

            if not changed:
                summary.unchanged += 1
                continue

            with db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET "
                    "application_stage = COALESCE(?, application_stage), "
                    "comp = CASE WHEN ? != '' THEN ? ELSE comp END "
                    "WHERE id = ?",
                    (stage, comp, comp, job_id),
                )
                conn.commit()
            summary.updated += 1

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return summary


def push_score(job_id: str) -> bool:
    """Push a job's current local Fit Score to its existing Notion row.
    Fit Score is an _initial_properties field (write-once on creation), so
    a later change needs this explicit push rather than the generic push()
    pipeline. Returns False if the job has no Notion page yet or no score
    set (run push() first in that case)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT score, notion_applications_page_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None or row["notion_applications_page_id"] is None or row["score"] is None:
        return False
    token, _ = _require_config()
    client = Client(auth=token)
    client.pages.update(
        page_id=row["notion_applications_page_id"],
        properties={PROP_FIT_SCORE: _number(row["score"])},
    )
    return True


def _find_existing_page(client: Client, data_source_id: str, job_id: str) -> str | None:
    """Fallback lookup by CareerOS Job ID, for when a job's stored
    notion_applications_page_id is missing (first push, or was cleared)."""
    resp = client.data_sources.query(
        data_source_id=data_source_id,
        filter={"property": PROP_JOB_ID, "rich_text": {"equals": job_id}},
        page_size=1,
    )
    results = resp.get("results", [])
    return results[0]["id"] if results else None


def push(
    status_filter: tuple[str, ...] = ("saved", "applied"),
    update_existing: bool = False,
) -> PushSummary:
    """Push all jobs with status in status_filter to the Notion Applications database.

    By default, a job that already has a Notion page (existing row) is left
    untouched entirely -- no properties written, not even bookkeeping ones.
    Only jobs with no existing page get a new row created. Pass
    update_existing=True to also refresh an existing row's bookkeeping
    fields (Job URL, Location, Date Applied, Last Synced) -- do this only
    when you explicitly ask, not as routine behavior.
    """
    token, data_source_id = _require_config()
    client = Client(auth=token)
    summary = PushSummary()

    now_iso = datetime.now(timezone.utc).date().isoformat()
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    placeholders = ",".join("?" * len(status_filter))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY status_at DESC",
            status_filter,
        ).fetchall()

    for row in rows:
        job = dict(row)
        summary.considered += 1

        page_id = job.get("notion_applications_page_id")
        if not page_id:
            page_id = _find_existing_page(client, data_source_id, job["id"])

        if page_id and not update_existing:
            # Existing row, no explicit opt-in to touch it: leave it alone.
            if not job.get("notion_applications_page_id"):
                # Found via fallback lookup but never recorded locally --
                # still worth storing the id so future lookups are direct,
                # but this writes nothing to Notion itself.
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET notion_applications_page_id = ? WHERE id = ?",
                        (page_id, job["id"]),
                    )
                    conn.commit()
            summary.unchanged += 1
            continue

        synced = _synced_properties(job, now_iso)
        try:
            if page_id:
                client.pages.update(page_id=page_id, properties=synced)
                summary.updated += 1
            else:
                created = client.pages.create(
                    parent={"data_source_id": data_source_id},
                    properties={**_initial_properties(job), **synced},
                )
                page_id = created["id"]
                summary.created += 1
        except Exception as exc:
            summary.skipped.append(f"{job['id']}: {exc}")
            continue

        with db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET notion_applications_page_id = ?, notion_last_synced_at = ? WHERE id = ?",
                (page_id, now_ts, job["id"]),
            )
            conn.commit()

    return summary
