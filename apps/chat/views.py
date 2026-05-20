"""Chat REST endpoints + SSE streaming.

Streaming uses Django's StreamingHttpResponse with NDJSON-style server-sent
events. No Channels/WebSocket dependency for chat — SSE is sufficient and
plays well with the existing frontend.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import IntentBlocked, QuotaExceeded, RateLimited
from apps.subscriptions.services import (
    check_daily_quota,
    check_intent_access,
    resolve_tier,
    subject_id_for,
)

from .models import ChatMessage, Conversation, FileAttachment
from .pipeline import PipelineContext, PipelineEvent, run_pipeline
from .serializers import (
    ChatMessageSerializer,
    ConversationSerializer,
    QuotaCheckRequestSerializer,
    SendMessageSerializer,
)

logger = logging.getLogger(__name__)


def _resolve_caller(request: Request) -> tuple[Any, str | None]:  # type: ignore[name-defined]
    """Return (user_or_None, guest_token_or_None) for the request."""
    user = request.user if request.user.is_authenticated else None
    guest = getattr(request, "guest_session", None)
    return user, (guest.token if guest else None)


class ConversationListCreateView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        user, guest_token = _resolve_caller(request)
        qs = Conversation.objects.filter(archived=False).order_by("-updated_at")
        if user:
            qs = qs.filter(user=user)
        elif guest_token:
            qs = qs.filter(guest_token=guest_token)
        else:
            return Response({"results": []})
        return Response({"results": ConversationSerializer(qs[:50], many=True).data})

    def post(self, request: Request) -> Response:
        user, guest_token = _resolve_caller(request)
        if not user and not guest_token:
            return Response(
                {"detail": "Auth or guest token required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        resolution = resolve_tier(user=user, guest_token=guest_token)
        conv = Conversation.objects.create(
            user=user, guest_token=guest_token or "",
            language=request.data.get("language", "en"),
            tier_at_start=resolution.tier,
        )
        return Response(
            ConversationSerializer(conv).data, status=status.HTTP_201_CREATED,
        )


class ConversationDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, conv_id: int) -> Response:
        conv = self._get_owned(request, conv_id)
        msgs = list(conv.messages.all())
        return Response({
            "conversation": ConversationSerializer(conv).data,
            "messages": ChatMessageSerializer(msgs, many=True).data,
        })

    def delete(self, request: Request, conv_id: int) -> Response:
        conv = self._get_owned(request, conv_id)
        conv.archived = True
        conv.save(update_fields=["archived"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_owned(self, request: Request, conv_id: int) -> Conversation:
        user, guest_token = _resolve_caller(request)
        conv = get_object_or_404(Conversation, id=conv_id)
        if user and conv.user_id == user.id:
            return conv
        if guest_token and conv.guest_token == guest_token:
            return conv
        # If admin, allow
        if user and getattr(user, "is_staff", False):
            return conv
        raise get_object_or_404(Conversation, id=-1)  # type: ignore[func-returns-value]


class QuotaCheckView(APIView):
    """Pre-flight check: would a chat message be allowed?"""

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        s = QuotaCheckRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user, guest_token = _resolve_caller(request)
        resolution = resolve_tier(user=user, guest_token=guest_token)
        subject = subject_id_for(user=user, guest_token=guest_token)
        quota = check_daily_quota(
            subject, resolution.config["daily_request_limit"], resolution.tier,
        )
        return Response({
            "allowed": quota.allowed,
            "tier": resolution.tier,
            "remaining": quota.remaining,
            "limit": quota.limit,
            "upgrade_cta": quota.upgrade_cta,
        })


def _sse_format(event: PipelineEvent) -> bytes:
    """Format a PipelineEvent as a single SSE message frame."""
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.type}\ndata: {payload}\n\n".encode("utf-8")


def _stream_events(generator: Iterator[PipelineEvent]) -> Iterator[bytes]:
    """Wrap a pipeline generator into SSE bytes, with error handling."""
    try:
        for event in generator:
            yield _sse_format(event)
    except QuotaExceeded as e:
        err = {"code": "quota_exceeded", "message": str(e), "upgrade_cta": e.upgrade_cta}
        yield _sse_format(PipelineEvent(type="error", data=err))
    except RateLimited as e:
        err = {"code": "rate_limited", "message": str(e), "retry_after": e.retry_after}
        yield _sse_format(PipelineEvent(type="error", data=err))
    except IntentBlocked as e:
        err = {"code": "intent_blocked", "message": str(e), "upgrade_cta": e.upgrade_cta}
        yield _sse_format(PipelineEvent(type="error", data=err))
    except Exception as e:  # noqa: BLE001
        logger.exception("chat_pipeline_failed")
        err = {"code": "internal", "message": "An unexpected error occurred."}
        yield _sse_format(PipelineEvent(type="error", data=err))


class SendMessageView(APIView):
    """POST /chat/conversations/{id}/messages/ — streams the response.

    Returns text/event-stream. Each event:
      meta | text | legal_basis | clarification | cta | done | error
    """

    permission_classes = [AllowAny]

    def post(self, request: Request, conv_id: int):
        s = SendMessageSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user, guest_token = _resolve_caller(request)
        if not user and not guest_token:
            return Response(
                {"detail": "Auth or guest token required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        conv = get_object_or_404(Conversation, id=conv_id)

        # Ownership check
        if user and conv.user_id and conv.user_id != user.id and not user.is_staff:
            return Response({"detail": "Not your conversation."}, status=403)
        if not user and guest_token and conv.guest_token != guest_token:
            return Response({"detail": "Not your conversation."}, status=403)

        attachments_payload = s.validated_data.get("attachments", []) or []
        attachments: list[FileAttachment] = []
        if attachments_payload:
            attachments = list(
                FileAttachment.objects.filter(
                    id__in=attachments_payload, conversation=conv,
                )
            )

        ctx = PipelineContext(
            user=user, guest_token=guest_token,
            conversation=conv,
            user_message=s.validated_data["user_message"],
            language=s.validated_data.get("language", conv.language),
            attachments=attachments,
        )

        response = StreamingHttpResponse(
            _stream_events(run_pipeline(ctx)),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # disable nginx buffering
        # response["Connection"] = "keep-alive"
        return response


class FileUploadView(APIView):
    """Max-tier file upload for document review."""

    permission_classes = [AllowAny]

    def post(self, request: Request, conv_id: int) -> Response:
        from pathlib import Path

        user, guest_token = _resolve_caller(request)
        conv = get_object_or_404(Conversation, id=conv_id)
        if not user or not user.is_authenticated:
            return Response(
                {"detail": "Sign in required for file upload."}, status=401,
            )
        resolution = resolve_tier(user=user)
        if not resolution.config["file_upload_allowed"]:
            return Response(
                {"detail": "File upload is available on Max tier."}, status=403,
            )

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "No file provided."}, status=400)
        if upload.size > 10 * 1024 * 1024:
            return Response({"detail": "File too large (10 MB max)."}, status=400)

        upload_dir = Path(settings.MEDIA_ROOT) / "chat-uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{conv.id}_{upload.name}"
        with open(target, "wb") as fh:
            for chunk in upload.chunks():
                fh.write(chunk)

        # Extract text (basic — DOCX or PDF)
        extracted = ""
        suffix = Path(upload.name).suffix.lower()
        try:
            if suffix == ".docx":
                from docx import Document as DocxDocument
                d = DocxDocument(str(target))
                extracted = "\n".join(p.text for p in d.paragraphs if p.text.strip())
            elif suffix == ".pdf":
                from pypdf import PdfReader
                r = PdfReader(str(target))
                extracted = "\n\n".join((p.extract_text() or "") for p in r.pages)
            elif suffix in (".txt", ".md"):
                extracted = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            logger.exception("file_extract_failed")

        att = FileAttachment.objects.create(
            conversation=conv, user=user,
            filename=upload.name,
            content_type=upload.content_type or "",
            size_bytes=upload.size,
            storage_path=str(target),
            extracted_text=extracted[:200_000],
        )
        return Response({
            "id": att.id,
            "filename": att.filename,
            "size_bytes": att.size_bytes,
        }, status=201)
