"""Admin endpoints under /api/v1/admin/."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import permissions
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import ChatMessage
from apps.subscriptions.models import UserSubscription, TierConfig
from apps.documents.models import Document
from apps.accounts.models import User

from .models import AuditEvent
from .serializers import AuditEventSerializer
from .services import verify_chain


class AuditEventListView(APIView):
    """Paginated audit log. Admin only."""

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        qs = AuditEvent.objects.select_related("actor").order_by("-id")
        event_type = request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)
        actor_id = request.query_params.get("actor_id")
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        # Simple slice
        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
        except ValueError:
            limit = 100
        return Response(
            {"results": AuditEventSerializer(qs[:limit], many=True).data}
        )


class ChainIntegrityView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        return Response(verify_chain())


class CostDashboardView(APIView):
    """Rolling 7/30 day Claude spend summary."""

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        now = timezone.now()
        windows = {"7d": 7, "30d": 30}
        out = {}
        for label, days in windows.items():
            since = now - timedelta(days=days)
            qs = ChatMessage.objects.filter(role="assistant", created_at__gte=since)
            agg = qs.aggregate(
                count=Count("id"),
                tokens_in=Sum("tokens_in"),
                tokens_out=Sum("tokens_out"),
            )
            out[label] = {
                "count": agg["count"] or 0,
                "tokens_in": agg["tokens_in"] or 0,
                "tokens_out": agg["tokens_out"] or 0,
            }
        # By tier
        per_tier = (
            ChatMessage.objects.filter(role="assistant", created_at__gte=now - timedelta(days=30))
            .values("conversation__tier_at_start")
            .annotate(count=Count("id"), tokens_out=Sum("tokens_out"))
        )
        out["by_tier"] = list(per_tier)
        return Response(out)


class SystemSummaryView(APIView):
    """Top-level admin dashboard numbers."""

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        return Response({
            "users": {
                "total": User.objects.count(),
                "premium": User.objects.filter(role__in=("premium_user", "staff", "admin")).count(),
                "verified": User.objects.filter(is_email_verified=True).count(),
            },
            "subscriptions": {
                "active": UserSubscription.objects.filter(status="active").count(),
                "by_tier": list(
                    UserSubscription.objects.filter(status="active")
                    .values("tier")
                    .annotate(n=Count("id"))
                ),
            },
            "documents": {
                "total": Document.objects.count(),
                "active": Document.objects.filter(status="active").count(),
            },
            "chat": {
                "messages_24h": ChatMessage.objects.filter(
                    created_at__gte=timezone.now() - timedelta(days=1)
                ).count(),
            },
        })
