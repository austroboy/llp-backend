"""Middleware for request tracing and structured logging."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("apps.common.middleware")


class RequestIdMiddleware(MiddlewareMixin):
    """Attach a stable X-Request-Id header to every request/response."""

    def process_request(self, request: HttpRequest) -> None:
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        request.request_id = rid  # type: ignore[attr-defined]

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        rid = getattr(request, "request_id", None)
        if rid:
            response["X-Request-Id"] = rid
        return response


class StructuredLoggingMiddleware:
    """Log every request with method, path, status, latency, user."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        user_id: Any = None
        if hasattr(request, "user") and request.user.is_authenticated:
            user_id = request.user.id

        logger.info(
            "http_request",
            extra={
                "request_id": getattr(request, "request_id", None),
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
                "user_id": user_id,
                "ip": request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR")),
            },
        )
        return response
