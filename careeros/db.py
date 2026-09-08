"""SQLite schema and connection helpers.

Design notes:
- The Notion Experience Bank is the human source of truth. `bank_records` mirrors it,
  keyed by Notion page ID so upserts are stable.
- Local enrichment (industry, role types, vetting decisions) lives in separate columns
  so a re-sync from Notion never clobbers work done locally.
- `companies`, `jobs`, and `applications` are created now for structure; they get
  populated in later phases.
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from careeros.config import DB_PATH, ensure_dirs


SCHEMA = """
-- Bank records mirror the Notion Experience Bank, keyed by Notion page ID.
CREATE TABLE IF NOT EXISTS bank_records (
    notion_page_id            TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    grain                     TEXT,          -- Role, Initiative, Skill, Certification, Award
    employers_json            TEXT,          -- JSON array
    timeframe                 TEXT,
    bullet_standard           TEXT,
    bullet_minimal            TEXT,
    bullet_ambitious          TEXT,
    metrics                   TEXT,
    notes                     TEXT,
    tech_tools_json           TEXT,          -- JSON array
    honesty_tag               TEXT,          -- Strong, Working, Gap, Never Claim, or NULL
    defensible_in_interview   INTEGER,       -- 0 or 1

    -- Local-only enrichment. Never overwritten by Notion sync.
    local_vetted_at           TEXT,          -- ISO datetime when vetted locally
    local_honesty_tag         TEXT,          -- override for Notion value
    local_defensible          INTEGER,       -- override for Notion value
    inferred_industry_json    TEXT,          -- JSON array
    inferred_role_types_json  TEXT,          -- JSON array

    -- Sync bookkeeping
    notion_last_edited        TEXT,
    synced_at                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_bank_records_grain ON bank_records(grain);
CREATE INDEX IF NOT EXISTS idx_bank_records_honesty ON bank_records(honesty_tag);


-- Immutable facts loaded from data/facts.yaml. Never edited by the system.
CREATE TABLE IF NOT EXISTS immutable_facts (
    key         TEXT PRIMARY KEY,   -- e.g. personal.name, education[0].institution
    value_json  TEXT NOT NULL,
    updated_at  TEXT
);


-- Target companies. Populated in Phase 2.
CREATE TABLE IF NOT EXISTS companies (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    tier         INTEGER,           -- 1 highest, 2, 3
    ats_type     TEXT,              -- greenhouse, lever, ashby
    ats_slug     TEXT,              -- their identifier on that ATS
    notes        TEXT,
    added_at     TEXT
);


-- Fetched jobs. Populated in Phase 2.
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,   -- ats_type::ats_job_id
    company_slug      TEXT REFERENCES companies(slug),
    ats_type          TEXT,
    ats_url           TEXT,
    title             TEXT,
    location          TEXT,
    remote_type       TEXT,               -- remote, hybrid, onsite, unknown
    description       TEXT,
    posted_at         TEXT,               -- ATS-reported posting/update date, normalized to ISO 8601 UTC
    fetched_at        TEXT,
    score             INTEGER,
    score_breakdown_json  TEXT,
    status            TEXT DEFAULT 'new', -- new, saved, rejected, applied
    status_at         TEXT,
    application_stage TEXT,               -- 1. Phone Screen, 2. First Round Interview, 3. Second Round Interview, 4. Final Round, 5. Offer, Rejected by Company, Withdrawn (only meaningful once status='applied'; empty means Notion's Status formula shows "Applied")
    application_stage_at  TEXT,
    date_applied      TEXT,               -- date (not datetime) Alex actually applied; distinct from status_at, which is when the DB row changed
    notion_applications_page_id  TEXT,    -- Notion page id in the 📋 Applications database, set on first push, used to upsert instead of duplicating
    notion_last_synced_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_slug);


-- Generated application packages. Populated in Phase 4.
CREATE TABLE IF NOT EXISTS applications (
    id           TEXT PRIMARY KEY,
    job_id       TEXT REFERENCES jobs(id),
    folder_path  TEXT,
    created_at   TEXT
);


-- Sync bookkeeping (last-synced timestamps and similar).
CREATE TABLE IF NOT EXISTS sync_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);
"""


def init_db() -> None:
    """Create schema if missing, then apply any column migrations. Idempotent."""
    ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a table's initial CREATE, for existing DBs.

    CREATE TABLE IF NOT EXISTS doesn't alter tables that already exist, so
    schema additions need an explicit ALTER TABLE here.
    """
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "posted_at" not in existing_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN posted_at TEXT")
    for col in (
        "application_stage",
        "application_stage_at",
        "date_applied",
        "notion_applications_page_id",
        "notion_last_synced_at",
    ):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with row factory set. Closes on exit."""
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def table_counts() -> dict[str, int]:
    """Return row count per user table. Used by `careeros status`."""
    tables = [
        "bank_records",
        "immutable_facts",
        "companies",
        "jobs",
        "applications",
        "sync_state",
    ]
    counts: dict[str, int] = {}
    with connect() as conn:
        for t in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
            counts[t] = row["n"]
    return counts
