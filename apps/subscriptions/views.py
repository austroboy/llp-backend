"""Subscription endpoints."""
from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_event
from apps.billing.services import create_checkout_session

from .models import QuotaCounter, TierConfig, UserSubscription
from .serializers import (
    TierConfigSerializer,
    UpgradeRequestSerializer,
    UserSubscriptionSerializer,
)
from .services import (
    check_daily_quota,
    get_tier_config,
    resolve_tier,
    subject_id_for,
)


class TierListView(APIView):
    """Public list of subscription tiers — used by the pricing page."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        tiers = TierConfig.objects.filter(is_active=True).order_by("price_bdt")
        return Response({"tiers": TierConfigSerializer(tiers, many=True).data})


class MeSubscriptionView(APIView):
    """Current user's active subscription + tier features."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        sub = (
            request.user.subscriptions.filter(status=UserSubscription.STATUS_ACTIVE)
            .order_by("-starts_at")
            .first()
        )
        resolution = resolve_tier(user=request.user)
        return Response(
            {
                "active_subscription": (
                    UserSubscriptionSerializer(sub).data if sub else None
                ),
                "effective_tier": resolution.tier,
                "tier_features": resolution.config,
            }
        )


class QuotaView(APIView):
    """Returns current daily usage."""

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        guest = getattr(request, "guest_session", None)
        guest_token = guest.token if guest else None
        resolution = resolve_tier(
            user=request.user if request.user.is_authenticated else None,
            guest_token=guest_token,
        )
        subject = subject_id_for(
            user=request.user if request.user.is_authenticated else None,
            guest_token=guest_token,
        )
        check = check_daily_quota(
            subject, resolution.config["daily_request_limit"], resolution.tier
        )
        return Response(
            {
                "tier": resolution.tier,
                "used": check.limit - check.remaining,
                "limit": check.limit,
                "remaining": check.remaining,
                "resets_in_seconds": check.retry_after_seconds or 0,
            }
        )


class UpgradeView(APIView):
    """Initiates a tier upgrade — returns a payment URL."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        s = UpgradeRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        target_tier = s.validated_data["target_tier"]
        provider = s.validated_data["payment_provider"]

        cfg = get_tier_config(target_tier)
        if not cfg.get("price_bdt"):
            return Response(
                {"detail": "Target tier is not purchasable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = create_checkout_session(
            user=request.user,
            target_tier=target_tier,
            amount_bdt=cfg["price_bdt"],
            provider=provider,
        )
        record_event(
            "billing.upgrade_initiated",
            actor=request.user,
            payload={"target_tier": target_tier, "provider": provider},
        )
        return Response(session)


class CancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        with transaction.atomic():
            sub = (
                request.user.subscriptions.select_for_update()
                .filter(status=UserSubscription.STATUS_ACTIVE)
                .first()
            )
            if not sub:
                return Response(
                    {"detail": "No active subscription."}, status=status.HTTP_404_NOT_FOUND
                )
            sub.auto_renew = False
            # Don't immediately set to cancelled — let it run out
            sub.save(update_fields=["auto_renew"])
        record_event(
            "billing.cancelled", actor=request.user, payload={"subscription_id": sub.id}
        )
        return Response({"detail": "Subscription will not renew."})
