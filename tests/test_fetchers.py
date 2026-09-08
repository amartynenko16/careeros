"""Tests for fetchers.

Filter logic runs against pure Python data. HTTP fetching is tested with
`httpx.MockTransport` so no network access is required.
"""

import json

import httpx
import pytest

from careeros.fetchers import ashby, greenhouse, lever
from careeros.fetchers.base import title_matches_role_filter


# --- title filter ----------------------------------------------------------

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Solutions Consultant", True),
        ("Solutions Engineer, Enterprise", True),
        ("Technical Account Manager", True),
        ("Technical Consultant", True),
        ("Implementation Manager", True),
        ("Professional Services Manager", True),
        ("Forward Deployed Engineer", True),
        ("Customer Solutions Engineer, NAMER", True),
        # Negatives — should NOT match
        ("Senior Software Engineer", False),
        ("Backend Engineer", False),
        ("Data Engineer", False),
        ("Machine Learning Engineer", False),
        ("Sales Engineer, Enterprise", False),
        ("Principal Sales Engineer, Datadog", False),
        ("Account Executive, Enterprise", False),
        ("SDR Manager", False),
        ("Engineering Manager, Platform", False),
        ("Product Designer", False),
        # No positive keyword → should not match
        ("Marketing Coordinator", False),
        ("HR Business Partner", False),
    ],
)
def test_title_filter(title: str, expected: bool) -> None:
    assert title_matches_role_filter(title) is expected


# --- Greenhouse fetcher -----------------------------------------------------

def test_greenhouse_fetch_parses_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": 12345,
                "absolute_url": "https://boards.greenhouse.io/example/jobs/12345",
                "title": "Solutions Consultant",
                "location": {"name": "Remote - Canada"},
                "departments": [{"name": "Customer Success"}],
                "content": "<p>We are hiring a <b>Solutions Consultant</b>.</p>",
                "updated_at": "2026-08-27T10:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "example" in str(request.url)
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)

    # Monkey-patch httpx.Client so the fetcher uses our transport
    original = httpx.Client

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", PatchedClient)

    jobs = greenhouse.fetch("example")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["ats_id"] == "12345"
    assert j["title"] == "Solutions Consultant"
    assert j["location"] == "Remote - Canada"
    assert j["remote_type"] == "remote"
    assert "Solutions Consultant" in j["description"]
    assert "<b>" not in j["description"]  # HTML stripped


# --- Lever fetcher ----------------------------------------------------------

def test_lever_fetch_parses_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "id": "abc-123",
            "hostedUrl": "https://jobs.lever.co/example/abc-123",
            "text": "Technical Account Manager",
            "categories": {
                "location": "Toronto, ON",
                "team": "Customer Success",
                "commitment": "hybrid",
            },
            "descriptionPlain": "Own the customer relationship end-to-end.",
            "updatedAt": 1700000000000,
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    original = httpx.Client

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", PatchedClient)

    jobs = lever.fetch("example")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["ats_id"] == "abc-123"
    assert j["title"] == "Technical Account Manager"
    assert j["location"] == "Toronto, ON"
    assert j["remote_type"] == "hybrid"


# --- Ashby fetcher ----------------------------------------------------------

def test_ashby_fetch_parses_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "jobs": [
            {
                "id": "job-xyz",
                "jobUrl": "https://jobs.ashbyhq.com/example/job-xyz",
                "title": "Implementation Manager",
                "location": "Remote (US, Canada)",
                "isRemote": True,
                "workplaceType": "Remote",
                "team": "Professional Services",
                "descriptionPlain": "Drive customer implementations.",
                "publishedAt": "2026-08-25T09:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    original = httpx.Client

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", PatchedClient)

    jobs = ashby.fetch("example")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["ats_id"] == "job-xyz"
    assert j["title"] == "Implementation Manager"
    assert j["remote_type"] == "remote"
    assert j["department"] == "Professional Services"
