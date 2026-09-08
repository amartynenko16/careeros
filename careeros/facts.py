"""Load immutable facts from data/facts.yaml into the immutable_facts table.

Facts are flattened to dotted-path keys so any field can be looked up by string:
    personal.name
    education[0].institution
    employers[2].title
    certifications[5].name

`load_from_yaml` is idempotent. Re-run any time facts.yaml changes.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from careeros import db
from careeros.config import FACTS_YAML_PATH


def _natural_key(s: str) -> list:
    """Natural sort key so certifications[2] sorts before certifications[10]."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", s)]


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Flatten nested dict/list to dotted-path keys.

    Lists use bracket notation: employers[0].name.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else k
            _flatten(key, v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            key = f"{prefix}[{i}]"
            _flatten(key, v, out)
    else:
        out[prefix] = value


def load_from_yaml(path: Path | None = None) -> int:
    """Load facts.yaml into the immutable_facts table. Returns row count written."""
    path = path or FACTS_YAML_PATH
    if not path.exists():
        raise FileNotFoundError(f"facts.yaml not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    flat: dict[str, Any] = {}
    _flatten("", data, flat)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        # Wipe and re-insert. Facts are immutable but the source file can be edited.
        conn.execute("DELETE FROM immutable_facts")
        conn.executemany(
            "INSERT INTO immutable_facts (key, value_json, updated_at) VALUES (?, ?, ?)",
            [(k, json.dumps(v), now) for k, v in flat.items()],
        )
        conn.commit()

    return len(flat)


def get(key: str) -> Any:
    """Return the value for a flattened key, or None if missing."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value_json FROM immutable_facts WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    return json.loads(row["value_json"])


def all_keys() -> list[str]:
    """Return all flattened keys, natural-sorted."""
    with db.connect() as conn:
        rows = conn.execute("SELECT key FROM immutable_facts").fetchall()
    return sorted((r["key"] for r in rows), key=_natural_key)


def sections() -> dict[str, dict[str, Any]]:
    """Return facts grouped by top-level section (personal, education, employers, certifications)."""
    result: dict[str, dict[str, Any]] = {}
    for k in all_keys():
        section = k.split(".", 1)[0].split("[", 1)[0]
        result.setdefault(section, {})[k] = get(k)
    return result
