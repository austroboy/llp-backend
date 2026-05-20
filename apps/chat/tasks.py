"""Celery tasks for chat app."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import Conversation, ResponseCache

logger = logging.getLogger(__name__)


@shared_task(name="apps.chat.tasks.archive_stale_conversations")
def archive_stale_conversations(days: int = 90) -> int:
    """Archive conversations with no activity for N days."""
    cutoff = timezone.now() - timedelta(days=days)
    qs = Conversation.objects.filter(archived=False, updated_at__lt=cutoff)
    n = qs.update(archived=True)
    logger.info("archived_stale_conversations", extra={"count": n, "days": days})
    return n


@shared_task(name="apps.chat.tasks.expire_response_cache")
def expire_response_cache() -> int:
    """Drop response cache rows whose expires_at has passed."""
    n, _ = ResponseCache.objects.filter(expires_at__lt=timezone.now()).delete()
    return n
