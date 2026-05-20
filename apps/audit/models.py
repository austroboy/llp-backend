"""Append-only audit event log with hash chaining."""
from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One row per recorded event. Append-only by convention; never UPDATE/DELETE.

    `hash` is sha256(prev_hash || event_canonical_json), forming a chain so that
    later tampering with an old row would invalidate every following row's hash.
    """

    EVENT_TYPES = [
        ("auth.register", "Auth: Register"),
        ("auth.login", "Auth: Login"),
        ("auth.logout", "Auth: Logout"),
        ("auth.password_reset", "Auth: Password reset"),
        ("auth.email_verified", "Auth: Email verified"),
        ("billing.upgrade_initiated", "Billing: Upgrade initiated"),
        ("billing.payment_received", "Billing: Payment received"),
        ("billing.cancelled", "Billing: Cancelled"),
        ("billing.refunded", "Billing: Refunded"),
        ("admin.doc_upload", "Admin: Document uploaded"),
        ("admin.doc_published", "Admin: Document published"),
        ("admin.user_action", "Admin: User action"),
        ("admin.tier_grant", "Admin: Tier granted"),
        ("admin.citation_resolved", "Admin: Citation resolved"),
        ("ai.generate", "AI: Response generated"),
        ("ai.classify", "AI: Intent classified"),
        ("ai.verify", "AI: Citation verified"),
        ("chat.quota_blocked", "Chat: Blocked by quota"),
        ("chat.intent_blocked", "Chat: Blocked by intent gate"),
    ]

    event_type = models.CharField(max_length=48, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    target_type = models.CharField(max_length=48, blank=True)
    target_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    request_id = models.CharField(max_length=32, blank=True, db_index=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_event"
        indexes = [
            models.Index(fields=["event_type", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.created_at}"
