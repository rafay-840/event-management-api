"""Tests for Pydantic request validation (all must answer 422)."""

import pytest

from tests.conftest import future_date


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"title": ""}, "empty title"),
        ({"title": "   "}, "title made of spaces"),
        ({"title": "x" * 201}, "title too long"),
        ({"event_date": "2020-01-01T10:00:00Z"}, "date in the past"),
        ({"event_date": "not-a-date"}, "invalid date"),
        ({"event_date": "2026-02-30T10:00:00Z"}, "impossible date"),
        ({"max_capacity": 0}, "capacity zero"),
        ({"max_capacity": -5}, "negative capacity"),
        ({"max_capacity": "many"}, "capacity not a number"),
        ({"location": ""}, "empty location"),
        ({"unexpected": "field"}, "unknown field"),
    ],
)
async def test_create_event_rejects_invalid_data(
    client, auth_headers, event_payload, override, reason
):
    response = await client.post("/events", json={**event_payload, **override}, headers=auth_headers)
    assert response.status_code == 422, reason


@pytest.mark.parametrize("missing", ["title", "event_date", "location", "max_capacity"])
async def test_create_event_requires_fields(client, auth_headers, event_payload, missing):
    del event_payload[missing]
    response = await client.post("/events", json=event_payload, headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", missing]


async def test_naive_date_is_accepted_as_utc(client, auth_headers, event_payload):
    naive = future_date().replace("+00:00", "")
    response = await client.post(
        "/events", json={**event_payload, "event_date": naive}, headers=auth_headers
    )
    assert response.status_code == 201
    assert response.json()["event_date"].endswith("Z")


@pytest.mark.parametrize(
    "body",
    [{}, {"title": None}, {"max_capacity": 0}, {"event_date": "2020-01-01T00:00:00Z"}],
)
async def test_update_event_rejects_invalid_data(client, auth_headers, create_event, body):
    event = await create_event()
    response = await client.put(f"/events/{event['id']}", json=body, headers=auth_headers)
    assert response.status_code == 422


async def test_update_allows_clearing_optional_description(client, auth_headers, create_event):
    event = await create_event()
    response = await client.put(
        f"/events/{event['id']}", json={"description": None}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["description"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"employee_name": "Ana", "employee_email": "not-an-email"},
        {"employee_name": "", "employee_email": "ana@acme.com"},
        {"employee_email": "ana@acme.com"},
        {"employee_name": "Ana"},
    ],
)
async def test_registration_rejects_invalid_data(client, create_event, body):
    event = await create_event()
    response = await client.post(f"/events/{event['id']}/registrations", json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/events/0", "/events/-1", "/events/abc"])
async def test_invalid_path_ids_return_422(client, path):
    response = await client.get(path)
    assert response.status_code == 422
