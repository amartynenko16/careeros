"""Shared pytest fixtures.

Geo-filter tests need TARGET_GEO_AREA/OPEN_REMOTE_TERMS pinned to known
values, not whatever happens to be in the local, gitignored
careeros/local_geo.py on the machine running the suite (which might be
someone's real commute area, an empty fallback on a fresh clone, or
anything else). This autouse fixture pins them for every test so the suite
behaves identically regardless of local setup.
"""

import pytest


@pytest.fixture(autouse=True)
def _pin_geo_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros.fetchers import base

    monkeypatch.setattr(base, "TARGET_GEO_AREA", ["toronto", "ontario"])
    monkeypatch.setattr(base, "OPEN_REMOTE_TERMS", ["global", "worldwide", "anywhere", "canada"])
