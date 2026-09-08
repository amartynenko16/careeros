"""Load target companies from data/companies.yaml into the companies table.

Idempotent: re-run any time companies.yaml changes.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from careeros import db
from careeros.config import DATA_DIR


COMPANIES_YAML_PATH = DATA_DIR / "companies.yaml"


def load_from_yaml(path: Path | None = None) -> int:
    """Load companies.yaml into the companies table. Returns row count written.

    Upserts by slug rather than wiping and re-inserting the whole table.
    A delete+reinsert breaks once any job has been triaged (saved, rejected,
    applied): those jobs FK-reference their company, so the DELETE fails
    with a FOREIGN KEY constraint error the moment any triage history
    exists, not just when the reloaded company itself changed. Upserting
    avoids that class of failure entirely; `careeros jobs clear` is no
    longer a prerequisite for this command.

    Companies removed from companies.yaml are deleted only if no job
    references them; if jobs do reference a removed company, that row is
    left in place (their history still needs it) rather than reloaded
    failing or silently orphaning those jobs.
    """
    path = path or COMPANIES_YAML_PATH
    if not path.exists():
        raise FileNotFoundError(f"companies.yaml not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    companies = data.get("companies") or []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    yaml_slugs = {c["slug"] for c in companies}

    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO companies (slug, name, tier, ats_type, ats_slug, notes, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name = excluded.name,
                tier = excluded.tier,
                ats_type = excluded.ats_type,
                ats_slug = excluded.ats_slug,
                notes = excluded.notes
            """,
            [
                (
                    c["slug"],
                    c["name"],
                    c.get("tier"),
                    c.get("ats_type"),
                    c.get("ats_slug", ""),
                    c.get("notes", ""),
                    now,
                )
                for c in companies
            ],
        )

        existing_slugs = {r["slug"] for r in conn.execute("SELECT slug FROM companies").fetchall()}
        removed_slugs = existing_slugs - yaml_slugs
        for slug in removed_slugs:
            try:
                conn.execute("DELETE FROM companies WHERE slug = ?", (slug,))
            except sqlite3.IntegrityError:
                pass  # still referenced by triaged jobs; leave it in place
        conn.commit()

    return len(companies)


def all_supported() -> list[dict[str, Any]]:
    """Return companies with a supported ATS (fetchable)."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM companies
            WHERE ats_type IN ('greenhouse', 'lever', 'ashby')
              AND ats_slug != ''
            ORDER BY tier, name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def all_unsupported() -> list[dict[str, Any]]:
    """Return companies with unsupported ATSs (skipped by scan)."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM companies
            WHERE ats_type = 'unsupported' OR ats_slug = ''
            ORDER BY tier, name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def summary() -> dict[str, int]:
    """Counts per tier and ATS type. Used by `careeros companies show`."""
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
        by_tier = {
            row["tier"]: row["n"]
            for row in conn.execute(
                "SELECT tier, COUNT(*) AS n FROM companies GROUP BY tier"
            ).fetchall()
        }
        by_ats = {
            row["ats_type"]: row["n"]
            for row in conn.execute(
                "SELECT ats_type, COUNT(*) AS n FROM companies GROUP BY ats_type"
            ).fetchall()
        }
    return {"total": total, "by_tier": by_tier, "by_ats": by_ats}
