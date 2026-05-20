"""Subscription, tier config, quota counter, and event models."""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from apps.common.models import TimestampedModel


class TierConfig(models.Model):
    """Editable tier definition. Seeded with DEFAULT_TIER_CONFIGS at first migrate."""

    tier = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=80)
    label_bn = models.CharField(max_length=120, blank=True)
    daily_request_limit = models.IntegerField()
    rate_limit_per_min = models.IntegerField()
    session_response_cap = models.IntegerField(null=True, blank=True)
    allowed_intents = ArrayField(models.CharField(max_length=32), default=list)
    file_upload_allowed = models.BooleanField(default=False)
    cross_domain_allowed = models.BooleanField(default=False)
    advisory_allowed = models.BooleanField(default=False)
    memory_window_days = models.IntegerField(default=0)
    zone2_max_rows = models.IntegerField(default=3)
    price_bdt = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions_tier_config"

    def __str__(self) -> str:
        return self.label

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "label": self.label,
            "label_bn": self.label_bn,
            "daily_request_limit": self.daily_request_limit,
            "rate_limit_per_min": self.rate_limit_per_min,
            "session_response_cap": self.session_response_cap,
            "allowed_intents": list(self.allowed_intents),
            "file_upload_allowed": self.file_upload_allowed,
            "cross_domain_allowed": self.cross_domain_allowed,
            "advisory_allowed": self.advisory_allowed,
            "memory_window_days": self.memory_window_days,
            "zone2_max_rows": self.zone2_max_rows,
            "price_bdt": self.price_bdt,
        }


class UserSubscription(TimestampedModel):
    """A user's subscription state. One active per user; history retained."""

    STATUS_ACTIVE = "active"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_OVERRIDDEN = "overridden"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_OVERRIDDEN, "Overridden"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    tier = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_subscriptions",
    )
    payment_ref = models.CharField(max_length=120, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "subscriptions_user_subscription"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def is_currently_active(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True


class QuotaCounter(models.Model):
    """Persisted daily quota usage. Redis is the live counter; this is the durable record."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="quota_counters",
    )
    guest_token = models.CharField(max_length=64, blank=True, db_index=True)
    tier = models.CharField(max_length=32)
    date = models.DateField(db_index=True)
    used = models.IntegerField(default=0)

    class Meta:
        db_table = "subscriptions_quota_counter"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date"],
                condition=models.Q(user__isnull=False),
                name="uniq_user_date_quota",
            ),
            models.UniqueConstraint(
                fields=["guest_token", "date"],
                condition=~models.Q(guest_token=""),
                name="uniq_guest_date_quota",
            ),
        ]


class SubscriptionEvent(models.Model):
    """Append-only history of subscription state changes."""

    EVENT_TYPES = [
        ("created", "Created"),
        ("upgraded", "Upgraded"),
        ("downgraded", "Downgraded"),
        ("cancelled", "Cancelled"),
        ("renewed", "Renewed"),
        ("expired", "Expired"),
        ("overridden", "Overridden"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription_events"
    )
    event_type = models.CharField(max_length=24, choices=EVENT_TYPES)
    from_tier = models.CharField(max_length=32, blank=True)
    to_tier = models.CharField(max_length=32)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "subscriptions_event"
        indexes = [models.Index(fields=["user", "-created_at"])]
