"""Central config: paths and env loading."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is the directory containing pyproject.toml.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
APPLICATIONS_DIR = PROJECT_ROOT / "applications"

DB_PATH = DATA_DIR / "careeros.db"
FACTS_YAML_PATH = DATA_DIR / "facts.yaml"

# Load .env if present. Silent if missing.
load_dotenv(PROJECT_ROOT / ".env")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID = os.getenv("NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID", "")
NOTION_APPLICATIONS_DATA_SOURCE_ID = os.getenv("NOTION_APPLICATIONS_DATA_SOURCE_ID", "")


def ensure_dirs() -> None:
    """Create runtime directories if missing."""
    for d in (DATA_DIR, LOGS_DIR, APPLICATIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)
