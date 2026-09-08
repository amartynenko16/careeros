"""Tests for Notion sync.

These do not hit the network. Property extraction is tested against fixture
pages that mirror the Notion API shape. Upsert is tested against an isolated
temp SQLite DB.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from careeros import db, notion_sync
from careeros.notion_sync import SyncSummary, _extract_page, _upsert


def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _fixture_page(**overrides: Any) -> dict[str, Any]:
    """A minimal Notion page mirroring the Experience Bank schema."""
    page: dict[str, Any] = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "last_edited_time": "2026-08-25T12:34:56.000Z",
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "Turned around at-risk enterprise account"}],
            },
            "Grain": {"type": "select", "select": {"name": "Initiative"}},
            "Employer": {
                "type": "multi_select",
                "multi_select": [{"name": "Globex Inc"}],
            },
            "Timeframe": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Q2 2025"}],
            },
            "Bullet - Standard": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Re-engaged a disengaged account, restoring trust."}],
            },
            "Bullet - Minimal": {"type": "rich_text", "rich_text": []},
            "Bullet - Ambitious": {"type": "rich_text", "rich_text": []},
            "Metrics": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "CSAT: perfect"}],
            },
            "Notes": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Cross-team recovery play."}],
            },
            "Tech and Tools": {
                "type": "multi_select",
                "multi_select": [{"name": "Acme APIs"}, {"name": "Liquid"}],
            },
            "Honesty Tag": {"type": "select", "select": {"name": "Strong"}},
            "Defensible": {"type": "checkbox", "checkbox": True},
        },
    }
    page.update(overrides)
    return page


def test_extract_page_full() -> None:
    rec = _extract_page(_fixture_page())
    assert rec["notion_page_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert rec["name"] == "Turned around at-risk enterprise account"
    assert rec["grain"] == "Initiative"
    assert json.loads(rec["employers_json"]) == ["Globex Inc"]
    assert rec["timeframe"] == "Q2 2025"
    assert rec["bullet_standard"].startswith("Re-engaged")
    assert rec["bullet_minimal"] is None
    assert rec["bullet_ambitious"] is None
    assert rec["metrics"] == "CSAT: perfect"
    assert rec["notes"] == "Cross-team recovery play."
    assert json.loads(rec["tech_tools_json"]) == ["Acme APIs", "Liquid"]
    assert rec["honesty_tag"] == "Strong"
    assert rec["defensible_in_interview"] == 1


def test_extract_page_handles_empty_fields() -> None:
    page = _fixture_page()
    page["properties"]["Grain"]["select"] = None
    page["properties"]["Honesty Tag"]["select"] = None
    page["properties"]["Defensible"]["checkbox"] = False
    page["properties"]["Employer"]["multi_select"] = []
    rec = _extract_page(page)
    assert rec["grain"] is None
    assert rec["honesty_tag"] is None
    assert rec["defensible_in_interview"] == 0
    assert json.loads(rec["employers_json"]) == []


def test_upsert_insert_then_update_preserves_local_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_db(tmp_path, monkeypatch)
    summary = SyncSummary()

    # Initial insert
    rec = _extract_page(_fixture_page())
    _upsert(rec, "2026-08-27T00:00:00+00:00", summary)
    assert summary.inserted == 1

    # Simulate local vetting between syncs
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE bank_records
               SET local_vetted_at = ?, local_honesty_tag = ?, local_defensible = ?
             WHERE notion_page_id = ?
            """,
            (
                "2026-08-27T01:00:00+00:00",
                "Strong",
                1,
                rec["notion_page_id"],
            ),
        )
        conn.commit()

    # Second sync with a changed Notion field
    page = _fixture_page()
    page["properties"]["Bullet - Standard"]["rich_text"][0]["plain_text"] = "Updated bullet."
    rec2 = _extract_page(page)
    summary2 = SyncSummary()
    _upsert(rec2, "2026-08-27T02:00:00+00:00", summary2)
    assert summary2.updated == 1

    # Local enrichment is preserved
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM bank_records WHERE notion_page_id = ?",
            (rec["notion_page_id"],),
        ).fetchone()
    assert row["bullet_standard"] == "Updated bullet."
    assert row["local_vetted_at"] == "2026-08-27T01:00:00+00:00"
    assert row["local_honesty_tag"] == "Strong"
    assert row["local_defensible"] == 1


def test_upsert_unchanged_reports_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_db(tmp_path, monkeypatch)
    rec = _extract_page(_fixture_page())
    now = "2026-08-27T00:00:00+00:00"
    s1 = SyncSummary()
    _upsert(rec, now, s1)
    s2 = SyncSummary()
    _upsert(rec, now, s2)
    assert s2.unchanged == 1
    assert s2.updated == 0


def test_require_config_raises_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notion_sync, "NOTION_TOKEN", "")
    monkeypatch.setattr(notion_sync, "NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID", "abc")
    with pytest.raises(notion_sync.NotionConfigError):
        notion_sync._require_config()


def test_require_config_raises_without_data_source_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notion_sync, "NOTION_TOKEN", "abc")
    monkeypatch.setattr(notion_sync, "NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID", "")
    with pytest.raises(notion_sync.NotionConfigError):
        notion_sync._require_config()
