"""Billing models: Invoice + WebhookEvent."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimestampedModel


class Invoice(TimestampedModel):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    PROVIDER_STRIPE = "stripe"
    PROVIDER_SSLCOMMERZ = "sslcommerz"
    PROVIDER_MANUAL = "manual"
    PROVIDER_CHOICES = [
        (PROVIDER_STRIPE, "Stripe"),
        (PROVIDER_SSLCOMMERZ, "SSLCommerz"),
        (PROVIDER_MANUAL, "Manual"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE, related_name="invoices",
    )
    subscription = models.ForeignKey(
        "subscriptions.UserSubscription",
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices",
    )
    target_tier = models.CharField(max_length=32)
    amount_bdt = models.IntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    provider_session_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider_ref = models.CharField(max_length=120, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_invoice"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]


class WebhookEvent(models.Model):
    """Raw webhook payloads for replay/audit. Kept forever."""

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=120, db_index=True)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField()
    signature = models.CharField(max_length=300, blank=True)
    processed = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_webhook_event"
        unique_together = [("provider", "event_id")]
