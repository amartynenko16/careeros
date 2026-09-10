"""Job triage: list, view, save, reject, mark applied.

Kept separate from scan so triage operations don't require a Notion sync or an
ATS call.
"""

from datetime import datetime, timezone
from typing import Any


from careeros import db


VALID_STATUSES = {"new", "saved", "rejected", "applied"}

# Post-application pipeline stages, distinct from VALID_STATUSES: status
# tracks CareerOS's own triage (did you decide to apply), stage tracks what
# happened after -- only meaningful once status='applied'. No "Applied" stage
# value: Notion's Status is now a formula read off Stage, defaulting to
# "Applied" whenever Stage is empty, so an applied-with-no-stage-set row is
# the normal/common case, not a gap to fill. Mirrors the Notion 📋
# Applications database's numbered "Stage" select options exactly (confirmed
# 2026-09-02); keep these in sync if that schema ever changes.
VALID_STAGES = {
    "1. Phone Screen", "2. First Round Interview", "2.5. Follow-Up Email",
    "3. Second Round Interview", "3.5. Follow-Up Email", "4. Final Round",
    "4.5. Follow-Up Email", "5. Offer", "Rejected by Company", "Withdrawn",
}

# Corrections to a job's remote_type persist here (local SQLite is what
# notion_apps.py pushes from), not in Notion directly -- notion-sync-applications
# does a full property overwrite on every push, so an edit made straight in
# Notion would just get reverted on the next sync.
VALID_REMOTE_TYPES = {"remote", "hybrid", "onsite", "unknown"}


class JobNotFoundError(ValueError):
    pass


class InvalidStageError(ValueError):
    pass


class InvalidStatusError(ValueError):
    pass


class InvalidRemoteTypeError(ValueError):
    pass


def list_jobs(
    status: str | None = None,
    company_slug: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return jobs matching the filters, most recently fetched first."""
    query = "SELECT * FROM jobs WHERE 1=1"
    args: list[Any] = []

    if status:
        if status not in VALID_STATUSES:
            raise InvalidStatusError(
                f"Unknown status '{status}'. Valid: {sorted(VALID_STATUSES)}"
            )
        query += " AND status = ?"
        args.append(status)

    if company_slug:
        query += " AND company_slug = ?"
        args.append(company_slug)

    query += " ORDER BY fetched_at DESC LIMIT ?"
    args.append(limit)

    with db.connect() as conn:
        rows = conn.execute(query, args).fetchall()
    return [dict(r) for r in rows]


def get(job_id: str) -> dict[str, Any]:
    """Return a single job by ID or raise JobNotFoundError."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobNotFoundError(f"No job with id '{job_id}'.")
    return dict(row)


def set_status(job_id: str, status: str) -> None:
    """Update a job's status. Raises on unknown job or invalid status.

    Marking a job 'applied' also stamps date_applied (today, once, if not
    already set) -- distinct from status_at, which just tracks when this DB
    row last changed and would get overwritten by an unrelated re-scan.
    """
    if status not in VALID_STATUSES:
        raise InvalidStatusError(
            f"Unknown status '{status}'. Valid: {sorted(VALID_STATUSES)}"
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        if status == "applied":
            cur = conn.execute(
                "UPDATE jobs SET status = ?, status_at = ?, "
                "date_applied = COALESCE(date_applied, ?) WHERE id = ?",
                (status, now, now[:10], job_id),
            )
        else:
            cur = conn.execute(
                "UPDATE jobs SET status = ?, status_at = ? WHERE id = ?",
                (status, now, job_id),
            )
        if cur.rowcount == 0:
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        conn.commit()


def set_application_stage(job_id: str, stage: str) -> None:
    """Update a job's post-application pipeline stage. Does not require
    status='applied' (you might record a stage slightly out of order), but
    the stage value itself must be one of VALID_STAGES."""
    if stage not in VALID_STAGES:
        raise InvalidStageError(
            f"Unknown stage '{stage}'. Valid: {sorted(VALID_STAGES)}"
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET application_stage = ?, application_stage_at = ? WHERE id = ?",
            (stage, now, job_id),
        )
        if cur.rowcount == 0:
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        conn.commit()


def set_remote_type(job_id: str, remote_type: str) -> None:
    """Correct a job's remote_type by hand (ATS data is sometimes wrong,
    especially on manually-added roles). Persists locally so it survives
    the next notion-sync-applications push, unlike an edit made in Notion."""
    if remote_type not in VALID_REMOTE_TYPES:
        raise InvalidRemoteTypeError(
            f"Unknown remote type '{remote_type}'. Valid: {sorted(VALID_REMOTE_TYPES)}"
        )
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET remote_type = ? WHERE id = ?",
            (remote_type, job_id),
        )
        if cur.rowcount == 0:
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        conn.commit()


def set_comp(job_id: str, comp: str) -> None:
    """Set a job's disclosed compensation. Only ever what the JD itself
    states (a range, a base figure, whatever form it took) -- never a
    researched or estimated figure. Persists locally so it survives the
    next notion-sync-applications push."""
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET comp = ? WHERE id = ?",
            (comp, job_id),
        )
        if cur.rowcount == 0:
            raise JobNotFoundError(f"No job with id '{job_id}'.")
        conn.commit()


def clear_new() -> int:
    """Delete all jobs with status='new'. Preserves saved / rejected / applied.

    Useful after adjusting filters or company assignments so the next scan
    starts clean without losing your triage work.
    """
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE status = 'new'")
        conn.commit()
        return cur.rowcount
