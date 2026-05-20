"""Billing endpoints: invoice list + webhook receivers."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Invoice, WebhookEvent
from .serializers import InvoiceSerializer
from .services import confirm_invoice

logger = logging.getLogger(__name__)


class InvoiceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = request.user.invoices.order_by("-created_at")[:100]
        return Response({"results": InvoiceSerializer(qs, many=True).data})


class StripeWebhookView(APIView):
    """Stripe webhook receiver. Verifies signature when configured."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request: Request) -> Response:
        payload = request.body
        sig_header = request.headers.get("Stripe-Signature", "")
        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

        # Parse + verify
        event = None
        if secret:
            try:
                import stripe  # type: ignore[import-untyped]

                event = stripe.Webhook.construct_event(payload, sig_header, secret)
                event_dict = event.to_dict_recursive()  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                logger.exception("stripe_webhook_verify_failed")
                return Response({"detail": str(e)}, status=400)
        else:
            try:
                event_dict = json.loads(payload.decode("utf-8"))
            except Exception:  # noqa: BLE001
                return Response({"detail": "invalid payload"}, status=400)

        event_id = event_dict.get("id", "")
        event_type = event_dict.get("type", "")

        # Idempotency: store and check
        we, created = WebhookEvent.objects.get_or_create(
            provider="stripe", event_id=event_id,
            defaults={
                "event_type": event_type,
                "payload": event_dict,
                "signature": sig_header,
            },
        )
        if not created and we.processed:
            return Response({"detail": "already processed"})

        try:
            if event_type in ("checkout.session.completed", "invoice.paid"):
                obj = event_dict.get("data", {}).get("object", {})
                invoice_id = obj.get("client_reference_id") or obj.get("metadata", {}).get("invoice_id")
                if invoice_id:
                    invoice = Invoice.objects.filter(id=int(invoice_id)).first()
                    if invoice:
                        confirm_invoice(invoice, provider_ref=obj.get("id", ""))
            we.processed = True
            from django.utils import timezone

            we.processed_at = timezone.now()
            we.save(update_fields=["processed", "processed_at"])
            return Response({"detail": "ok"})
        except Exception as e:  # noqa: BLE001
            logger.exception("stripe_webhook_processing_failed")
            we.error_message = str(e)
            we.save(update_fields=["error_message"])
            return Response({"detail": str(e)}, status=500)


class SSLCommerzWebhookView(APIView):
    """SSLCommerz IPN receiver."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request: Request) -> Response:
        data = request.data if isinstance(request.data, dict) else {}
        invoice_id = data.get("tran_id") or data.get("value_a")
        ref = data.get("bank_tran_id", "")
        # NOTE: A production deployment must verify the IPN against SSLCommerz
        # by re-querying their validator endpoint. We record the raw event
        # for audit and confirm on success status only.
        if not invoice_id:
            return Response({"detail": "missing tran_id"}, status=400)
        WebhookEvent.objects.create(
            provider="sslcommerz",
            event_id=str(invoice_id),
            event_type=str(data.get("status", "unknown")),
            payload=data,
        )
        if str(data.get("status", "")).lower() == "valid":
            invoice = Invoice.objects.filter(id=int(invoice_id)).first()
            if invoice:
                confirm_invoice(invoice, provider_ref=ref)
        return Response({"detail": "ok"})


class ManualConfirmInvoiceView(APIView):
    """Admin-only manual confirmation. Useful for ops correction or testing."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, invoice_id: int) -> Response:
        if not request.user.is_staff:
            return Response({"detail": "Admin only."}, status=403)
        invoice = get_object_or_404(Invoice, id=invoice_id)
        confirm_invoice(invoice, provider_ref="manual")
        return Response(InvoiceSerializer(invoice).data)
