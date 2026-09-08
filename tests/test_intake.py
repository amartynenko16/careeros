"""Tests for the non-network parts of intake.py: URL parsing and the
promoted geo_eligible() heuristic (previously rebuilt ad hoc by hand
several times during the 2026-09-01 session)."""

import pytest

from careeros.intake import geo_eligible, parse_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://jobs.ashbyhq.com/docker/8fba5588-e4cf-449f-b953-74f7985863cd", ("ashby", "docker", "8fba5588-e4cf-449f-b953-74f7985863cd")),
        ("https://jobs.ashbyhq.com/docker/8fba5588-e4cf-449f-b953-74f7985863cd/application?utm_source=x", ("ashby", "docker", "8fba5588-e4cf-449f-b953-74f7985863cd")),
        ("https://boards.greenhouse.io/gitlab/jobs/8687171002", ("greenhouse", "gitlab", "8687171002")),
        ("https://job-boards.greenhouse.io/vercel/jobs/6121381004", ("greenhouse", "vercel", "6121381004")),
        ("https://jobs.lever.co/deliverect/ec294766-fb58-4dd9-8c2f-c18019f599ca", ("lever", "deliverect", "ec294766-fb58-4dd9-8c2f-c18019f599ca")),
        ("https://example.workday.com/some/job/12345", None),
        ("not a url at all", None),
    ],
)
def test_parse_url(url: str, expected) -> None:
    assert parse_url(url) == expected


@pytest.mark.parametrize(
    "location,title,remote_type,expected",
    [
        ("Toronto, Ontario, Canada", "Senior TAM", "remote", True),
        ("Remote (United States | Canada)", "Senior TAM", "remote", True),
        ("Remote, Turkey", "Senior TAM", "remote", False),
        ("Remote, Global", "Senior TAM", "remote", True),
        ("", "Senior TAM", "remote", True),
        ("Toronto", "Senior TAM", "onsite", True),
        ("Austin, US", "Senior TAM", "onsite", False),
        ("Distributed", "Senior Customer Engineer, Majors - Toronto, CA", "onsite", True),
        ("Hybrid", "Senior TAM", "hybrid", False),
    ],
)
def test_geo_eligible(location: str, title: str, remote_type: str, expected: bool) -> None:
    assert geo_eligible(location, title, remote_type) is expected


def test_geo_eligible_amer_region_segment_is_open() -> None:
    # "AMER" as its own comma-separated segment is an ATS region tag for the
    # Americas (includes Canada), not a US-only restriction. Real case:
    # Supabase's "Remote, AMER" (2026-09-04).
    assert geo_eligible("Remote, AMER", "Customer Solution Architect", "remote") is True


def test_geo_eligible_amer_substring_inside_unrelated_word_not_matched() -> None:
    # "amer" must be matched as a whole segment, not a bare substring --
    # "Cameroon" contains "amer" as a substring but isn't a region tag.
    assert geo_eligible("Remote, Cameroon", "Customer Solution Architect", "remote") is False


def test_geo_eligible_description_overrides_open_looking_location() -> None:
    # Fivetran (2026-09-04): location field said "Remote, Colorado, United
    # States, AMER" (looks open thanks to the AMER tag) but the JD body
    # hard-restricts to US physical residency. The description-level check
    # must override the location-field read.
    desc = "To be eligible for this role, you must be physically located in the United States."
    assert geo_eligible("Remote, Colorado, United States, AMER", "Resident Solutions Architect", "remote", desc) is False


def test_geo_eligible_description_timezone_restriction() -> None:
    # Temporal (2026-09-04): location field just named a hiring manager's
    # city ("Boston, Massachusetts"), no US-only text in the field itself --
    # the restriction only appears in the description body.
    desc = "Travel expected to be 25-50%. Reside within the Eastern time zone (United States)."
    assert geo_eligible("Boston, Massachusetts", "Staff Solutions Architect", "remote", desc) is False


def test_geo_eligible_benefits_boilerplate_not_mistaken_for_restriction() -> None:
    # Cloudflare-style benefits boilerplate mentions "United States" without
    # restricting the role to it -- must not false-positive.
    desc = "Benefits described here are for employees in the United States, and vary for employees based outside the U.S."
    assert geo_eligible("Remote, Global", "Senior TAM", "remote", desc) is True
