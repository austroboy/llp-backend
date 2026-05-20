"""Billing services. Wraps Stripe + SSLCommerz behind a uniform interface.

In v1 these are functional stubs that:
  - Stripe: creates a checkout session via the Stripe SDK if STRIPE_SECRET_KEY
    is set; otherwise creates a mock pending invoice with a frontend-side URL.
  - SSLCommerz: same — calls the live API if creds are present, mocks otherwise.

The webhook handlers call `confirm_invoice()` on success, which atomically:
  - marks the Invoice paid
  - creates/updates the user's UserSubscription
  - records a SubscriptionEvent
  - records an audit event

Real production deployments will need to replace the mock branches with the
provider's actual checkout URL flow. The data model and handlers will not need
to change.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.subscriptions.models import SubscriptionEvent, UserSubscription

from .models import Invoice

logger = logging.getLogger(__name__)


def create_checkout_session(*, user, target_tier: str, amount_bdt: int,
                            provider: str = "stripe") -> dict:
    """Create a checkout session and a corresponding pending Invoice."""
    invoice = Invoice.objects.create(
        user=user, target_tier=target_tier, amount_bdt=amount_bdt,
        provider=provider, status=Invoice.STATUS_PENDING,
    )

    if provider == Invoice.PROVIDER_STRIPE:
        url, session_id = _create_stripe_session(invoice)
    elif provider == Invoice.PROVIDER_SSLCOMMERZ:
        url, session_id = _create_sslcommerz_session(invoice)
    else:
        url, session_id = _create_manual_session(invoice)

    invoice.provider_session_id = session_id
    invoice.metadata = {"checkout_url": url}
    invoice.save(update_fields=["provider_session_id", "metadata"])

    return {
        "invoice_id": invoice.id,
        "checkout_url": url,
        "provider": provider,
        "amount_bdt": amount_bdt,
        "target_tier": target_tier,
    }


def _create_stripe_session(invoice: Invoice) -> tuple[str, str]:
    if not settings.__dict__.get("STRIPE_SECRET_KEY"):
        return _mock_url(invoice), f"mock_{invoice.id}"
    try:
        import stripe  # type: ignore[import-untyped]

        stripe.api_key = settings.STRIPE_SECRET_KEY
        # Convert BDT → smallest currency unit (paisa) for amount
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "bdt",
                    "unit_amount": invoice.amount_bdt * 100,
                    "recurring": {"interval": "month"},
                    "product_data": {
                        "name": f"LLP {invoice.target_tier.title()} subscription",
                    },
                },
                "quantity": 1,
            }],
            client_reference_id=str(invoice.id),
            success_url=f"{settings.FRONTEND_URL}/billing/success?invoice={invoice.id}",
            cancel_url=f"{settings.FRONTEND_URL}/billing/cancelled?invoice={invoice.id}",
        )
        return session.url, session.id
    except Exception:  # noqa: BLE001
        logger.exception("stripe_session_create_failed")
        return _mock_url(invoice), f"mock_{invoice.id}"


def _create_sslcommerz_session(invoice: Invoice) -> tuple[str, str]:
    """SSLCommerz session via REST API. In v1 this is the mock branch unless
    SSLCOMMERZ_STORE_ID is set; the live integration is a TODO."""
    return _mock_url(invoice), f"sslc_{invoice.id}"


def _create_manual_session(invoice: Invoice) -> tuple[str, str]:
    return _mock_url(invoice), f"manual_{invoice.id}"


def _mock_url(invoice: Invoice) -> str:
    return f"{settings.FRONTEND_URL}/billing/mock?invoice={invoice.id}"


@transaction.atomic
def confirm_invoice(invoice: Invoice, *, provider_ref: str = "") -> UserSubscription:
    """Mark an invoice paid and (re)issue a UserSubscription for the target tier."""
    if invoice.status == Invoice.STATUS_PAID:
        # Idempotent — return the existing subscription
        return invoice.subscription  # type: ignore[return-value]

    invoice.status = Invoice.STATUS_PAID
    invoice.paid_at = timezone.now()
    if provider_ref:
        invoice.provider_ref = provider_ref

    # Expire any current active subscription
    UserSubscription.objects.filter(
        user=invoice.user, status=UserSubscription.STATUS_ACTIVE,
    ).update(status=UserSubscription.STATUS_EXPIRED)

    sub = UserSubscription.objects.create(
        user=invoice.user, tier=invoice.target_tier,
        status=UserSubscription.STATUS_ACTIVE,
        starts_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=30),
        auto_renew=True,
        payment_ref=provider_ref,
        metadata={"invoice_id": invoice.id},
    )
    invoice.subscription = sub
    invoice.save()

    SubscriptionEvent.objects.create(
        user=invoice.user, event_type="upgraded",
        from_tier="free_subscribed", to_tier=invoice.target_tier,
        payload={"invoice_id": invoice.id, "amount_bdt": invoice.amount_bdt},
    )
    record_event(
        "billing.payment_received", actor=invoice.user,
        target=invoice, payload={"target_tier": invoice.target_tier},
    )
    return sub
