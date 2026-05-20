"""Exception classes + DRF exception handler returning RFC 7807 problem+json."""
from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

logger = logging.getLogger(__name__)


class QuotaExceeded(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Daily quota exceeded."
    default_code = "quota_exceeded"

    def __init__(self, detail: str | None = None, upgrade_cta: dict | None = None):
        super().__init__(detail or self.default_detail, code=self.default_code)
        self.upgrade_cta = upgrade_cta


class IntentBlocked(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This intent is not available on your tier."
    default_code = "intent_blocked"

    def __init__(self, detail: str | None = None, upgrade_cta: dict | None = None):
        super().__init__(detail or self.default_detail, code=self.default_code)
        self.upgrade_cta = upgrade_cta


class RateLimited(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many requests."
    default_code = "rate_limited"

    def __init__(self, detail: str | None = None, retry_after: int | None = None):
        super().__init__(detail or self.default_detail, code=self.default_code)
        self.retry_after = retry_after


class CorpusEmpty(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document corpus is not loaded."
    default_code = "corpus_empty"


def problem_json_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Render exceptions as RFC 7807 problem+json."""
    response = drf_default_handler(exc, context)
    request = context.get("request")
    request_id = getattr(request, "request_id", None) if request else None

    if response is None:
        # Unhandled exception — log and return a generic 500
        logger.exception("unhandled_exception", extra={"request_id": request_id})
        return Response(
            data={
                "type": "/errors/internal",
                "title": "Internal server error",
                "status": 500,
                "detail": "An unexpected error occurred.",
                "request_id": request_id,
            },
            status=500,
            content_type="application/problem+json",
        )

    code = getattr(exc, "default_code", "error")
    detail = response.data.get("detail") if isinstance(response.data, dict) else str(response.data)
    payload: dict[str, Any] = {
        "type": f"/errors/{code}",
        "title": str(exc.default_detail) if isinstance(exc, APIException) else "Error",
        "status": response.status_code,
        "detail": detail,
        "request_id": request_id,
    }
    if isinstance(exc, QuotaExceeded) and exc.upgrade_cta:
        payload["upgrade_cta"] = exc.upgrade_cta
    if isinstance(exc, IntentBlocked) and exc.upgrade_cta:
        payload["upgrade_cta"] = exc.upgrade_cta
    if isinstance(exc, RateLimited) and exc.retry_after is not None:
        response["Retry-After"] = str(exc.retry_after)
        payload["retry_after"] = exc.retry_after

    response.data = payload
    response["Content-Type"] = "application/problem+json"
    return response
