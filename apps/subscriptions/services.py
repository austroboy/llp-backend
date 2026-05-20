"""Quota tracking and rate limiting against Redis.

Redis is the live counter — it's the source of truth in-day. A Celery task
periodically syncs counts back to Postgres for durability.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django_redis import get_redis_connection

from .constants import CTA_MESSAGES
from .models import TierConfig

logger = logging.getLogger(__name__)


@dataclass
class TierResolution:
    """Result of resolving a request's tier."""

    tier: str
    config: dict
    user_id: Optional[int] = None
    guest_token: Optional[str] = None


@dataclass
class QuotaCheckResult:
    allowed: bool
    reason: str = ""
    remaining: int = 0
    limit: int = 0
    retry_after_seconds: int = 0
    upgrade_cta: dict | None = None


def _today_str() -> str:
    return date.today().isoformat()


def _quota_key(subject_id: str) -> str:
    return f"quota:{subject_id}:{_today_str()}"


def _rate_key(subject_id: str) -> str:
    minute_bucket = datetime.utcnow().strftime("%Y%m%d%H%M")
    return f"rate:{subject_id}:{minute_bucket}"


def _seconds_until_midnight() -> int:
    now = datetime.utcnow()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def get_tier_config(tier_name: str) -> dict:
    """Read tier config from cache, falling back to DB.

    The cache is invalidated whenever a TierConfig is saved (signal).
    """
    cache_key = f"tier_config:{tier_name}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    try:
        config = TierConfig.objects.get(tier=tier_name, is_active=True)
    except TierConfig.DoesNotExist:
        # Fall back to defaults if DB hasn't been seeded
        from .constants import DEFAULT_TIER_CONFIGS
        for default in DEFAULT_TIER_CONFIGS:
            if default["tier"] == tier_name:
                cache.set(cache_key, default, 60)
                return default
        raise
    payload = config.to_dict()
    cache.set(cache_key, payload, 300)
    return payload


def resolve_tier(*, user=None, guest_token: str | None = None) -> TierResolution:
    """Determine the active tier for a request."""
    from .constants import Tier

    if user and user.is_authenticated:
        # Look up active subscription
        sub = (
            user.subscriptions.filter(status="active")
            .order_by("-starts_at")
            .first()
        )
        if sub and sub.is_currently_active():
            tier_name = sub.tier
        else:
            tier_name = Tier.FREE_SUBSCRIBED
        return TierResolution(
            tier=tier_name, config=get_tier_config(tier_name), user_id=user.id
        )
    return TierResolution(
        tier=Tier.FREE_GUEST,
        config=get_tier_config(Tier.FREE_GUEST),
        guest_token=guest_token,
    )


def check_rate_limit(subject_id: str, limit_per_min: int) -> QuotaCheckResult:
    """Sliding-minute token bucket. Returns Allow or Block."""
    if not settings.RATE_LIMIT_ENABLED:
        return QuotaCheckResult(allowed=True)
    try:
        conn = get_redis_connection("default")
        key = _rate_key(subject_id)
        used = conn.incr(key)
        if used == 1:
            conn.expire(key, 60)
        if used > limit_per_min:
            ttl = conn.ttl(key) or 60
            return QuotaCheckResult(
                allowed=False,
                reason="rate_limit_minute",
                retry_after_seconds=int(ttl),
                limit=limit_per_min,
            )
        return QuotaCheckResult(
            allowed=True, remaining=max(0, limit_per_min - used), limit=limit_per_min
        )
    except Exception:  # noqa: BLE001
        # Fail-open if Redis is unreachable. Logged but doesn't block traffic.
        logger.warning("rate_limit_check_failed", extra={"subject": subject_id})
        return QuotaCheckResult(allowed=True)


def check_daily_quota(subject_id: str, daily_limit: int, current_tier: str) -> QuotaCheckResult:
    """Check whether the subject has remaining daily quota."""
    try:
        conn = get_redis_connection("default")
        key = _quota_key(subject_id)
        used_raw = conn.get(key)
        used = int(used_raw) if used_raw else 0
    except Exception:  # noqa: BLE001
        logger.warning("quota_check_failed", extra={"subject": subject_id})
        return QuotaCheckResult(allowed=True, remaining=daily_limit, limit=daily_limit)

    remaining = max(0, daily_limit - used)
    if used >= daily_limit:
        cta = CTA_MESSAGES.get(current_tier)
        return QuotaCheckResult(
            allowed=False,
            reason="daily_quota",
            remaining=0,
            limit=daily_limit,
            retry_after_seconds=_seconds_until_midnight(),
            upgrade_cta=dict(cta) if cta else None,
        )
    return QuotaCheckResult(allowed=True, remaining=remaining, limit=daily_limit)


def consume_quota(subject_id: str, count: int = 1) -> int:
    """Increment the quota counter. Returns new total."""
    try:
        conn = get_redis_connection("default")
        key = _quota_key(subject_id)
        new = conn.incrby(key, count)
        conn.expire(key, _seconds_until_midnight() + 3600)  # cushion
        return int(new)
    except Exception:  # noqa: BLE001
        logger.warning("quota_consume_failed", extra={"subject": subject_id})
        return 0


def check_intent_access(intent: str, tier_config: dict, current_tier: str) -> QuotaCheckResult:
    """Verify the intent is allowed on this tier."""
    if intent in tier_config["allowed_intents"]:
        return QuotaCheckResult(allowed=True)
    cta = CTA_MESSAGES.get(current_tier)
    return QuotaCheckResult(
        allowed=False,
        reason="intent_blocked",
        upgrade_cta=dict(cta) if cta else None,
    )


def subject_id_for(*, user=None, guest_token: str | None = None) -> str:
    """Stable subject ID for keying Redis counters."""
    if user and user.is_authenticated:
        return f"u:{user.id}"
    if guest_token:
        return f"g:{guest_token}"
    return "anon"
