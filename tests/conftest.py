"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.subscriptions.constants import DEFAULT_TIER_CONFIGS
from apps.subscriptions.models import TierConfig, UserSubscription


@pytest.fixture(autouse=True)
def seeded_tiers(db):
    """Auto-seed tier configs for every test."""
    for cfg in DEFAULT_TIER_CONFIGS:
        TierConfig.objects.update_or_create(
            tier=cfg["tier"],
            defaults={k: v for k, v in cfg.items() if k != "tier"},
        )
    yield


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="user@example.com", password="testpass1234", full_name="Test User",
    )


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        email="admin@example.com", password="adminpass1234", full_name="Admin",
    )


@pytest.fixture
def authed_client(api_client, user):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def admin_client(api_client, admin_user):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def mini_user(user):
    """A user with an active Mini subscription."""
    from django.utils import timezone
    UserSubscription.objects.create(
        user=user, tier="mini", status="active",
        starts_at=timezone.now(),
        expires_at=timezone.now() + timezone.timedelta(days=30),
    )
    return user


@pytest.fixture
def max_user(user):
    """A user with an active Max subscription."""
    from django.utils import timezone
    UserSubscription.objects.create(
        user=user, tier="max", status="active",
        starts_at=timezone.now(),
        expires_at=timezone.now() + timezone.timedelta(days=30),
    )
    return user
