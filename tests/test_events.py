"""Tests for event CRUD and its authentication requirements."""

import pytest

from tests.conftest import future_date


async def test_create_event(client, auth_headers, event_payload):
    response = await client.post("/events", json=event_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["title"] == event_payload["title"]
    assert body["max_capacity"] == 3
    assert body["registration_count"] == 0
    assert body["available_seats"] == 3


async def test_list_events_empty(client):
    response = await client.get("/events")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_events_is_public_and_ordered_by_date(client, create_event):
    later = await create_event(title="Later", event_date=future_date(days=20))
    sooner = await create_event(title="Sooner", event_date=future_date(days=5))

    response = await client.get("/events")  # no auth header

    assert response.status_code == 200
    assert [e["id"] for e in response.json()] == [sooner["id"], later["id"]]


async def test_get_event(client, create_event):
    event = await create_event()
    response = await client.get(f"/events/{event['id']}")
    assert response.status_code == 200
    assert response.json() == event


async def test_get_missing_event_returns_404(client):
    response = await client.get("/events/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Event with id 9999 not found"


async def test_update_event_changes_only_sent_fields(client, auth_headers, create_event):
    event = await create_event()

    response = await client.put(
        f"/events/{event['id']}", json={"location": "Auditorium"}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["location"] == "Auditorium"
    assert body["title"] == event["title"]  # untouched
    assert body["max_capacity"] == event["max_capacity"]  # untouched


async def test_update_missing_event_returns_404(client, auth_headers):
    response = await client.put("/events/9999", json={"title": "X"}, headers=auth_headers)
    assert response.status_code == 404


async def test_delete_event(client, auth_headers, create_event):
    event = await create_event()

    response = await client.delete(f"/events/{event['id']}", headers=auth_headers)

    assert response.status_code == 204
    assert response.content == b""
    assert (await client.get(f"/events/{event['id']}")).status_code == 404


async def test_delete_missing_event_returns_404(client, auth_headers):
    response = await client.delete("/events/9999", headers=auth_headers)
    assert response.status_code == 404


async def test_delete_event_also_deletes_its_registrations(
    client, auth_headers, create_event, register
):
    event = await create_event()
    registration = (await register(event["id"], "a@acme.com")).json()

    await client.delete(f"/events/{event['id']}", headers=auth_headers)

    response = await client.delete(f"/registrations/{registration['id']}")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/events", {"title": "X"}),
        ("PUT", "/events/1", {"title": "X"}),
        ("DELETE", "/events/1", None),
    ],
)
async def test_write_operations_require_authentication(client, method, path, body):
    response = await client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
