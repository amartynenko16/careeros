"""Pull the Notion Experience Bank into local SQLite.

Design:
- One-way: Notion is the source of truth; local mirrors it.
- Upsert keyed on `notion_page_id`.
- Local-only enrichment columns (local_vetted_at, local_honesty_tag, local_defensible,
  inferred_industry_json, inferred_role_types_json) are never touched by sync.
- Full re-pull each run. The Bank is small enough that incremental sync is not worth
  the complexity in Phase 1.
- Two-way sync (pushing local edits back to Notion) is out of scope for now.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from notion_client import Client

from careeros import db
from careeros.config import NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID, NOTION_TOKEN


# Notion property names as they appear in the 🏦 Experience Bank schema.
# If you rename a Notion property, update the constant here, not the extractor code.
PROP_NAME = "Name"
PROP_GRAIN = "Grain"
PROP_EMPLOYER = "Employer"
PROP_TIMEFRAME = "Timeframe"
PROP_BULLET_STANDARD = "Bullet - Standard"
PROP_BULLET_MINIMAL = "Bullet - Minimal"
PROP_BULLET_AMBITIOUS = "Bullet - Ambitious"
PROP_METRICS = "Metrics"
PROP_NOTES = "Notes"
PROP_TECH_TOOLS = "Tech and Tools"
PROP_HONESTY_TAG = "Honesty Tag"
PROP_DEFENSIBLE = "Defensible"


@dataclass
class SyncSummary:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)


class NotionConfigError(RuntimeError):
    """Raised when required Notion config is missing."""


def _require_config() -> tuple[str, str]:
    if not NOTION_TOKEN:
        raise NotionConfigError(
            "NOTION_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    if not NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID:
        raise NotionConfigError(
            "NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID is not set. See .env.example."
        )
    return NOTION_TOKEN, NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID


# --- Property extractors -----------------------------------------------------

def _plain_text(rich: list[dict[str, Any]] | None) -> str | None:
    """Concatenate a Notion rich_text array to a plain string."""
    if not rich:
        return None
    return "".join(chunk.get("plain_text", "") for chunk in rich) or None


def _title(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    return _plain_text(prop.get("title"))


def _rich_text(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    return _plain_text(prop.get("rich_text"))


def _select_name(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    select = prop.get("select")
    return select.get("name") if select else None


def _multi_select_names(prop: dict[str, Any] | None) -> list[str]:
    if not prop:
        return []
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def _checkbox(prop: dict[str, Any] | None) -> int:
    if not prop:
        return 0
    return 1 if prop.get("checkbox") else 0


def _extract_page(page: dict[str, Any]) -> dict[str, Any]:
    """Convert a Notion page object to our bank_records column dict."""
    props = page.get("properties", {})
    return {
        "notion_page_id": page["id"],
        "name": _title(props.get(PROP_NAME)) or "(untitled)",
        "grain": _select_name(props.get(PROP_GRAIN)),
        "employers_json": json.dumps(_multi_select_names(props.get(PROP_EMPLOYER))),
        "timeframe": _rich_text(props.get(PROP_TIMEFRAME)),
        "bullet_standard": _rich_text(props.get(PROP_BULLET_STANDARD)),
        "bullet_minimal": _rich_text(props.get(PROP_BULLET_MINIMAL)),
        "bullet_ambitious": _rich_text(props.get(PROP_BULLET_AMBITIOUS)),
        "metrics": _rich_text(props.get(PROP_METRICS)),
        "notes": _rich_text(props.get(PROP_NOTES)),
        "tech_tools_json": json.dumps(_multi_select_names(props.get(PROP_TECH_TOOLS))),
        "honesty_tag": _select_name(props.get(PROP_HONESTY_TAG)),
        "defensible_in_interview": _checkbox(props.get(PROP_DEFENSIBLE)),
        "notion_last_edited": page.get("last_edited_time"),
    }


# --- Fetching ---------------------------------------------------------------

def _fetch_all_pages(client: Client, data_source_id: str) -> list[dict[str, Any]]:
    """Query every page in the Notion data source, handling pagination."""
    pages: list[dict[str, Any]] = []
    start_cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = client.data_sources.query(**kwargs)
        pages.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        start_cursor = response.get("next_cursor")
    return pages


# --- Upsert -----------------------------------------------------------------

# Columns that mirror Notion. These get overwritten on every sync.
_NOTION_COLUMNS = (
    "name",
    "grain",
    "employers_json",
    "timeframe",
    "bullet_standard",
    "bullet_minimal",
    "bullet_ambitious",
    "metrics",
    "notes",
    "tech_tools_json",
    "honesty_tag",
    "defensible_in_interview",
    "notion_last_edited",
)


def _upsert(record: dict[str, Any], now: str, summary: SyncSummary) -> None:
    """Insert or update a bank_record. Local enrichment columns are preserved."""
    page_id = record["notion_page_id"]
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT " + ", ".join(_NOTION_COLUMNS) + " FROM bank_records WHERE notion_page_id = ?",
            (page_id,),
        ).fetchone()

        if existing is None:
            columns = ["notion_page_id"] + list(_NOTION_COLUMNS) + ["synced_at"]
            placeholders = ", ".join(["?"] * len(columns))
            values = [page_id] + [record[c] for c in _NOTION_COLUMNS] + [now]
            conn.execute(
                f"INSERT INTO bank_records ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            summary.inserted += 1
        else:
            # Detect changes for reporting; the update runs either way to refresh synced_at.
            changed = any(existing[c] != record[c] for c in _NOTION_COLUMNS)
            set_clause = ", ".join(f"{c} = ?" for c in _NOTION_COLUMNS) + ", synced_at = ?"
            values = [record[c] for c in _NOTION_COLUMNS] + [now, page_id]
            conn.execute(
                f"UPDATE bank_records SET {set_clause} WHERE notion_page_id = ?",
                values,
            )
            if changed:
                summary.updated += 1
            else:
                summary.unchanged += 1

        conn.commit()


def _record_sync_state(summary: SyncSummary, now: str) -> None:
    """Store last-sync bookkeeping in sync_state."""
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
            ("bank_last_synced_at", now, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
            (
                "bank_last_sync_summary",
                json.dumps(
                    {
                        "fetched": summary.fetched,
                        "inserted": summary.inserted,
                        "updated": summary.updated,
                        "unchanged": summary.unchanged,
                    }
                ),
                now,
            ),
        )
        conn.commit()


# --- Public entrypoints -----------------------------------------------------

def sync(dry_run: bool = False) -> SyncSummary:
    """Full re-pull from Notion Experience Bank into local SQLite.

    Args:
        dry_run: If True, fetch and report counts but do not write to the DB.
    """
    token, data_source_id = _require_config()
    client = Client(auth=token)

    pages = _fetch_all_pages(client, data_source_id)
    summary = SyncSummary(fetched=len(pages))

    if dry_run:
        # Still parse to surface property-mapping errors early.
        for page in pages:
            try:
                _extract_page(page)
            except Exception as exc:
                summary.warnings.append(f"Extract failed for page {page.get('id')}: {exc}")
        return summary

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for page in pages:
        try:
            record = _extract_page(page)
        except Exception as exc:
            summary.warnings.append(f"Extract failed for page {page.get('id')}: {exc}")
            continue
        _upsert(record, now, summary)

    _record_sync_state(summary, now)
    return summary
