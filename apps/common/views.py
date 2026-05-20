"""Health check endpoints. /api/health/ + /api/health/deep/."""
from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe. Cheap and fast — always returns 200 if the process is up."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "service": "llp-backend"})


class DeepHealthView(APIView):
    """Readiness probe. Checks DB + cache. Used by load balancers."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        results: dict[str, dict] = {}

        # DB
        t0 = time.perf_counter()
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
            results["database"] = {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            results["database"] = {"ok": False, "error": str(e)}

        # Cache
        t0 = time.perf_counter()
        try:
            cache.set("health:probe", "ok", 5)
            ok = cache.get("health:probe") == "ok"
            results["cache"] = {
                "ok": ok,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }
        except Exception as e:  # noqa: BLE001
            results["cache"] = {"ok": False, "error": str(e)}

        # AI keys present?
        results["anthropic"] = {"configured": bool(settings.ANTHROPIC_API_KEY)}
        results["gemini"] = {"configured": bool(settings.GEMINI_API_KEY)}

        all_ok = all(r.get("ok", True) for r in results.values())
        return Response(
            {"status": "ok" if all_ok else "degraded", "checks": results},
            status=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
