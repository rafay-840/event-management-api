"""Tests for pagination (skip/limit) and search/filtering (title, date)."""

from datetime import datetime, timedelta, timezone

import pytest

from db import settings
from tests.conftest import future_date


@pytest.fixture(autouse=True)
def utc_day_boundaries(monkeypatch):
    """Make the ``?date=`` filter use UTC days, whatever the local .env says."""
    monkeypatch.setattr(settings, "app_timezone", "UTC")


async def test_default_limit_is_10_and_total_header(client, create_event):
    for day in range(1, 13):
        await create_event(title=f"Event {day}", event_date=future_date(days=day))

    response = await client.get("/events")

    assert response.status_code == 200
    assert len(response.json()) == 10
    assert response.headers["x-total-count"] == "12"


async def test_skip_and_limit(client, create_event):
    for day in range(1, 6):
        await create_event(title=f"Event {day}", event_date=future_date(days=day))

    response = await client.get("/events", params={"skip": 1, "limit": 2})

    assert [e["title"] for e in response.json()] == ["Event 2", "Event 3"]


async def test_skip_past_the_end_returns_empty_page(client, create_event):
    await create_event()
    response = await client.get("/events", params={"skip": 50})
    assert response.json() == []
    assert response.headers["x-total-count"] == "1"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"skip": -1}])
async def test_invalid_pagination_returns_422(client, params):
    response = await client.get("/events", params=params)
    assert response.status_code == 422


async def test_title_filter_is_partial_and_case_insensitive(client, create_event):
    await create_event(title="Python for Beginners")
    await create_event(title="Advanced PYTHON")
    await create_event(title="Team Lunch")

    response = await client.get("/events", params={"title": "python"})

    assert sorted(e["title"] for e in response.json()) == [
        "Advanced PYTHON",
        "Python for Beginners",
    ]


async def test_title_filter_treats_wildcards_literally(client, create_event):
    await create_event(title="100% Launch")
    await create_event(title="Launch Party")

    response = await client.get("/events", params={"title": "%"})

    assert [e["title"] for e in response.json()] == ["100% Launch"]


async def test_date_filter(client, create_event):
    target = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    on_day_morning = await create_event(title="Morning", event_date=f"{target}T00:00:00Z")
    on_day_evening = await create_event(title="Evening", event_date=f"{target}T23:59:00Z")
    await create_event(title="Next day", event_date=f"{target + timedelta(days=1)}T00:00:00Z")

    response = await client.get("/events", params={"date": str(target)})

    assert [e["id"] for e in response.json()] == [on_day_morning["id"], on_day_evening["id"]]


async def test_date_filter_respects_configured_time_zone(client, create_event):
    target = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    # 23:30 UTC is already the next day in Rome (UTC+1 in winter, +2 in summer).
    await create_event(title="Late UTC", event_date=f"{target}T23:30:00Z")

    settings.app_timezone = "Europe/Rome"  # restored by the autouse fixture
    in_rome_next_day = await client.get("/events", params={"date": str(target + timedelta(1))})
    in_rome_same_day = await client.get("/events", params={"date": str(target)})

    assert [e["title"] for e in in_rome_next_day.json()] == ["Late UTC"]
    assert in_rome_same_day.json() == []


async def test_filters_combine_with_each_other_and_pagination(client, create_event):
    target = (datetime.now(timezone.utc) + timedelta(days=7)).date()
    for hour in (9, 11, 13):
        await create_event(title=f"Python {hour}", event_date=f"{target}T{hour:02d}:00:00Z")
    await create_event(title="Yoga", event_date=f"{target}T10:00:00Z")
    await create_event(title="Python other day", event_date=future_date(days=8))

    response = await client.get(
        "/events", params={"title": "python", "date": str(target), "skip": 1, "limit": 1}
    )

    assert [e["title"] for e in response.json()] == ["Python 11"]
    assert response.headers["x-total-count"] == "3"


async def test_invalid_date_filter_returns_422(client):
    response = await client.get("/events", params={"date": "2026-13-01"})
    assert response.status_code == 422


async def test_registrations_are_paginated(client, create_event, register):
    event = await create_event(max_capacity=5)
    for i in range(5):
        await register(event["id"], f"user{i}@acme.com")

    response = await client.get(
        f"/events/{event['id']}/registrations", params={"skip": 3, "limit": 10}
    )

    assert [r["employee_email"] for r in response.json()] == ["user3@acme.com", "user4@acme.com"]
    assert response.headers["x-total-count"] == "5"
