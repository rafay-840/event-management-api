"""Tests for sign-up, login and token validation."""

from datetime import datetime, timedelta, timezone

import jwt

from db import settings
from tests.conftest import TEST_USER


async def test_register_returns_user_without_password(client):
    response = await client.post("/auth/register", json={**TEST_USER, "email": "Ana@Acme.COM"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@acme.com"  # normalised to lower case
    assert "password" not in body and "hashed_password" not in body


async def test_register_duplicate_email_returns_409(client):
    await client.post("/auth/register", json=TEST_USER)
    response = await client.post(
        "/auth/register", json={**TEST_USER, "email": TEST_USER["email"].upper()}
    )
    assert response.status_code == 409


async def test_register_rejects_short_password(client):
    response = await client.post("/auth/register", json={**TEST_USER, "password": "short"})
    assert response.status_code == 422


async def test_login_returns_working_token(client):
    await client.post("/auth/register", json=TEST_USER)
    response = await client.post(
        "/auth/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == TEST_USER["email"]


async def test_login_wrong_password_returns_401(client):
    await client.post("/auth/register", json=TEST_USER)
    response = await client.post(
        "/auth/login", data={"username": TEST_USER["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_login_unknown_email_returns_401(client):
    response = await client.post(
        "/auth/login", data={"username": "nobody@acme.com", "password": "whatever123"}
    )
    assert response.status_code == 401
    # Same message as a wrong password: do not reveal which accounts exist.
    assert response.json()["detail"] == "Incorrect email or password"


async def test_me_without_token_returns_401(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401


async def test_malformed_token_returns_401(client):
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_expired_token_returns_401(client, auth_headers):
    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


async def test_token_signed_with_other_secret_returns_401(client, auth_headers):
    forged = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "attacker-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
