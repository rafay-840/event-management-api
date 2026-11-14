"""Tests for registrations and their business rules."""

import asyncio

import services


async def test_register_for_event(client, create_event, register):
    event = await create_event()

    response = await register(event["id"], "Ana@Acme.com", name="Ana")

    assert response.status_code == 201
    body = response.json()
    assert body["event_id"] == event["id"]
    assert body["employee_email"] == "ana@acme.com"

    updated = (await client.get(f"/events/{event['id']}")).json()
    assert updated["registration_count"] == 1
    assert updated["available_seats"] == event["max_capacity"] - 1


async def test_list_registrations(client, create_event, register):
    event = await create_event()
    await register(event["id"], "a@acme.com")
    await register(event["id"], "b@acme.com")

    response = await client.get(f"/events/{event['id']}/registrations")

    assert response.status_code == 200
    assert [r["employee_email"] for r in response.json()] == ["a@acme.com", "b@acme.com"]


async def test_list_registrations_of_missing_event_returns_404(client):
    response = await client.get("/events/9999/registrations")
    assert response.status_code == 404


async def test_duplicate_registration_returns_409(create_event, register):
    event = await create_event()
    await register(event["id"], "ana@acme.com")

    # Different letter case is still the same person.
    response = await register(event["id"], "ANA@acme.com")

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


async def test_same_employee_can_join_different_events(create_event, register):
    first, second = await create_event(), await create_event(title="Other")
    assert (await register(first["id"], "ana@acme.com")).status_code == 201
    assert (await register(second["id"], "ana@acme.com")).status_code == 201


async def test_full_event_returns_409(create_event, register):
    event = await create_event(max_capacity=2)
    await register(event["id"], "a@acme.com")
    await register(event["id"], "b@acme.com")

    response = await register(event["id"], "c@acme.com")

    assert response.status_code == 409
    assert "full" in response.json()["detail"]


async def test_register_for_missing_event_returns_404(register):
    response = await register(9999, "ana@acme.com")
    assert response.status_code == 404


async def test_cancel_registration_frees_a_seat(client, create_event, register):
    event = await create_event(max_capacity=1)
    registration = (await register(event["id"], "a@acme.com")).json()
    assert (await register(event["id"], "b@acme.com")).status_code == 409  # full

    response = await client.delete(f"/registrations/{registration['id']}")

    assert response.status_code == 204
    assert (await register(event["id"], "b@acme.com")).status_code == 201


async def test_cancel_missing_registration_returns_404(client):
    response = await client.delete("/registrations/9999")
    assert response.status_code == 404


async def test_capacity_cannot_drop_below_registrations(
    client, auth_headers, create_event, register
):
    event = await create_event(max_capacity=3)
    await register(event["id"], "a@acme.com")
    await register(event["id"], "b@acme.com")

    too_low = await client.put(
        f"/events/{event['id']}", json={"max_capacity": 1}, headers=auth_headers
    )
    exact = await client.put(
        f"/events/{event['id']}", json={"max_capacity": 2}, headers=auth_headers
    )

    assert too_low.status_code == 409
    assert exact.status_code == 200


async def test_concurrent_registrations_never_exceed_capacity(
    client, create_event, register, monkeypatch
):
    """Ten employees race for the last seat; exactly one must win.

    Each request runs in its own database session/connection. To make the race
    reliable, we slow down the gap between "count the seats" and "insert": every
    request pauses there, so without the ``SELECT ... FOR UPDATE`` lock all ten
    would see a free seat and the event would be over-booked.
    """
    event = await create_event(max_capacity=1)
    original_count = services.count_registrations

    async def slow_count(*args, **kwargs):
        result = await original_count(*args, **kwargs)
        await asyncio.sleep(0.05)  # widen the race window
        return result

    monkeypatch.setattr(services, "count_registrations", slow_count)

    responses = await asyncio.gather(
        *(register(event["id"], f"user{i}@acme.com") for i in range(10))
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 9
    registrations = (await client.get(f"/events/{event['id']}/registrations")).json()
    assert len(registrations) == 1
