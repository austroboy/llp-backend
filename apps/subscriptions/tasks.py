"""Celery tasks for subscriptions."""
from __future__ import annotations

import logging
from datetime import date

from celery import shared_task
from django.utils import timezone
from django_redis import get_redis_connection

from .models import QuotaCounter, UserSubscription

logger = logging.getLogger(__name__)


@shared_task(name="apps.subscriptions.tasks.reconcile_quota_counters")
def reconcile_quota_counters() -> dict:
    """Periodically write Redis quota counters back to Postgres for durability.

    Scans `quota:*` keys for today and upserts a QuotaCounter row each.
    """
    conn = get_redis_connection("default")
    today = date.today()
    today_str = today.isoformat()
    count = 0

    for key in conn.scan_iter(match=f"*quota:*:{today_str}", count=200):
        try:
            key_str = key.decode() if isinstance(key, bytes) else key
            # Format: "<prefix>quota:<subject>:<date>"
            parts = key_str.split("quota:")[-1].split(":")
            subject = parts[0]
            used_raw = conn.get(key)
            used = int(used_raw) if used_raw else 0
            if subject.startswith("u:"):
                user_id = int(subject[2:])
                QuotaCounter.objects.update_or_create(
                    user_id=user_id, date=today,
                    defaults={"used": used, "tier": "unknown"},
                )
            elif subject.startswith("g:"):
                guest_token = subject[2:]
                QuotaCounter.objects.update_or_create(
                    guest_token=guest_token, date=today,
                    defaults={"used": used, "tier": "free_guest"},
                )
            count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("reconcile_failed_for_key", extra={"key": str(key), "err": str(e)})
    return {"reconciled": count, "date": today_str}


@shared_task(name="apps.subscriptions.tasks.expire_subscriptions")
def expire_subscriptions() -> dict:
    """Mark subscriptions whose expires_at has passed as expired."""
    now = timezone.now()
    qs = UserSubscription.objects.filter(
        status=UserSubscription.STATUS_ACTIVE,
        expires_at__lt=now,
    )
    updated = qs.update(status=UserSubscription.STATUS_EXPIRED)
    return {"expired": updated}
