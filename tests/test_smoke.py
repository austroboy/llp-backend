"""Smoke tests — minimum confirmation that the app boots and core endpoints work."""
from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_health_liveness(api_client):
    """The bare liveness probe returns 200 always."""
    res = api_client.get("/api/health/")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "llp-backend"}


@pytest.mark.django_db
def test_health_deep(api_client):
    """The readiness probe returns either 200 (ok) or 503 (degraded)."""
    res = api_client.get("/api/health/deep/")
    assert res.status_code in (200, 503)
    body = res.json()
    assert "status" in body
    assert "checks" in body
    assert "database" in body["checks"]


@pytest.mark.django_db
def test_tier_list_public(api_client):
    """The pricing page can fetch tier configs without auth."""
    res = api_client.get("/api/v1/subscriptions/tiers/")
    assert res.status_code == 200
    body = res.json()
    assert "tiers" in body
    assert len(body["tiers"]) >= 4
    tier_names = {t["tier"] for t in body["tiers"]}
    assert {"free_guest", "free_subscribed", "mini", "max"} <= tier_names


@pytest.mark.django_db
def test_register_login_flow(api_client):
    """A user can register and log in."""
    payload = {
        "email": "smoke@example.com",
        "password": "stringly-typed-password-1234",
        "full_name": "Smoke Test",
    }
    res = api_client.post("/api/v1/auth/register/", payload, format="json")
    assert res.status_code == 201, res.content
    body = res.json()
    assert "user" in body
    assert "tokens" in body
    assert body["tokens"]["access"]
    assert body["tokens"]["refresh"]

    # Login with the same credentials
    res = api_client.post(
        "/api/v1/auth/login/",
        {"email": payload["email"], "password": payload["password"]},
        format="json",
    )
    assert res.status_code == 200
    assert res.json()["tokens"]["access"]


@pytest.mark.django_db
def test_guest_token_issuance(api_client):
    """Anonymous visitors can mint a guest token."""
    res = api_client.post("/api/v1/auth/guest/", {"language": "en"}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["guest_token"]
    assert len(body["guest_token"]) >= 30


@pytest.mark.django_db
def test_me_requires_auth(api_client):
    """`/auth/me/` should 401 without auth."""
    res = api_client.get("/api/v1/auth/me/")
    assert res.status_code == 401


@pytest.mark.django_db
def test_me_returns_profile(authed_client, user):
    """`/auth/me/` returns the authenticated user's profile."""
    res = authed_client.get("/api/v1/auth/me/")
    assert res.status_code == 200
    assert res.json()["email"] == user.email


@pytest.mark.django_db
def test_admin_endpoints_require_admin(authed_client):
    """Non-admin can't read the audit log."""
    res = authed_client.get("/api/v1/admin/audit/")
    assert res.status_code in (401, 403)


@pytest.mark.django_db
def test_admin_audit_works(admin_client):
    """Admin can read the audit log."""
    res = admin_client.get("/api/v1/admin/audit/")
    assert res.status_code == 200
    assert "results" in res.json()
