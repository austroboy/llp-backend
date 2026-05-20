"""Tier gating + quota enforcement tests."""
from __future__ import annotations

import pytest

from apps.subscriptions.constants import Intent, Tier
from apps.subscriptions.services import (
    check_intent_access,
    get_tier_config,
    resolve_tier,
)


@pytest.mark.django_db
def test_default_tier_for_anonymous():
    """An unauthenticated request resolves to free_guest."""
    res = resolve_tier(user=None, guest_token=None)
    assert res.tier == Tier.FREE_GUEST


@pytest.mark.django_db
def test_default_tier_for_registered(user):
    """A registered user with no subscription resolves to free_subscribed."""
    res = resolve_tier(user=user)
    assert res.tier == Tier.FREE_SUBSCRIBED


@pytest.mark.django_db
def test_tier_for_mini_subscriber(mini_user):
    res = resolve_tier(user=mini_user)
    assert res.tier == Tier.MINI


@pytest.mark.django_db
def test_tier_for_max_subscriber(max_user):
    res = resolve_tier(user=max_user)
    assert res.tier == Tier.MAX


@pytest.mark.django_db
@pytest.mark.parametrize("tier,intent,allowed", [
    # Free tiers — only basic intents
    (Tier.FREE_GUEST, Intent.FACTUAL, True),
    (Tier.FREE_GUEST, Intent.PROCEDURAL, True),
    (Tier.FREE_GUEST, Intent.PRODUCT_INQUIRY, True),
    (Tier.FREE_GUEST, Intent.CALCULATION, False),
    (Tier.FREE_GUEST, Intent.DRAFTING, False),
    (Tier.FREE_GUEST, Intent.ADVISORY, False),

    (Tier.FREE_SUBSCRIBED, Intent.CALCULATION, True),
    (Tier.FREE_SUBSCRIBED, Intent.DRAFTING, False),
    (Tier.FREE_SUBSCRIBED, Intent.ADVISORY, False),

    # Mini — adds drafting and cross-domain, no advisory
    (Tier.MINI, Intent.DRAFTING, True),
    (Tier.MINI, Intent.CROSS_DOMAIN, True),
    (Tier.MINI, Intent.ADVISORY, True),  # in allowed list per default config

    # Max — everything except NOT_A_QUESTION
    (Tier.MAX, Intent.ADVISORY, True),
    (Tier.MAX, Intent.DRAFTING, True),
    (Tier.MAX, Intent.CROSS_DOMAIN, True),
])
def test_intent_access_matrix(tier, intent, allowed):
    cfg = get_tier_config(tier)
    res = check_intent_access(intent, cfg, tier)
    assert res.allowed is allowed, (
        f"Expected intent {intent} on tier {tier} to be "
        f"{'allowed' if allowed else 'blocked'}; got {res.allowed}"
    )


@pytest.mark.django_db
def test_tier_features_in_config():
    """Each tier has the right capability flags."""
    free_cfg = get_tier_config(Tier.FREE_SUBSCRIBED)
    assert not free_cfg["file_upload_allowed"]
    assert free_cfg["zone2_max_rows"] == 3

    mini_cfg = get_tier_config(Tier.MINI)
    assert not mini_cfg["file_upload_allowed"]
    assert mini_cfg["cross_domain_allowed"]
    assert mini_cfg["zone2_max_rows"] == 4

    max_cfg = get_tier_config(Tier.MAX)
    assert max_cfg["file_upload_allowed"]
    assert max_cfg["advisory_allowed"]
    assert max_cfg["cross_domain_allowed"]
    assert max_cfg["zone2_max_rows"] == 6


@pytest.mark.django_db
def test_quota_endpoint_works(authed_client):
    res = authed_client.get("/api/v1/subscriptions/quota/")
    assert res.status_code == 200
    body = res.json()
    assert "tier" in body
    assert "limit" in body
    assert "remaining" in body
    assert body["limit"] >= body["remaining"] >= 0


@pytest.mark.django_db
def test_my_subscription_includes_tier_features(authed_client):
    res = authed_client.get("/api/v1/subscriptions/me/")
    assert res.status_code == 200
    body = res.json()
    assert body["effective_tier"] == Tier.FREE_SUBSCRIBED
    assert "tier_features" in body
    assert "allowed_intents" in body["tier_features"]
