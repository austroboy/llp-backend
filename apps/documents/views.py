"""Document admin endpoints."""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_event

from .models import CitationAudit, Document, IngestionJob, Node
from .serializers import (
    CitationAuditResolveSerializer,
    CitationAuditSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    IngestionJobSerializer,
    NodeSerializer,
    NodeUpdateSerializer,
    SidebarSerializer,
)
from .tasks import (
    ingest_corpus_zip_task,
    ingest_docx_task,
    ingest_pdf_task,
    reembed_node_task,
)

logger = logging.getLogger(__name__)


class DocumentListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        docs = Document.objects.all().order_by("doc_code")
        return Response({"results": DocumentSerializer(docs, many=True).data})


class DocumentDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request, doc_code: str) -> Response:
        doc = get_object_or_404(Document, doc_code=doc_code)
        return Response(DocumentSerializer(doc).data)


class DocumentUploadView(APIView):
    """POST a .docx, .pdf, .json or .zip to ingest."""

    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser]

    def post(self, request: Request) -> Response:
        s = DocumentUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        upload = s.validated_data["file"]
        doc_code = s.validated_data["doc_code"]

        # Save to a known directory the worker can read
        import os
        upload_dir = Path(settings.MEDIA_ROOT) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{doc_code}_{upload.name}"
        with open(target, "wb") as fh:
            for chunk in upload.chunks():
                fh.write(chunk)

        ext = Path(upload.name).suffix.lower()
        if ext == ".zip":
            job = IngestionJob.objects.create(
                job_type="bulk_zip", source_path=str(target),
                submitted_by=request.user,
            )
            ingest_corpus_zip_task.delay(job.id, str(target))
        elif ext == ".docx":
            job = IngestionJob.objects.create(
                job_type="docx", source_path=str(target),
                submitted_by=request.user,
            )
            ingest_docx_task.delay(
                job.id, str(target), doc_code,
                s.validated_data.get("title", ""),
                s.validated_data.get("language", "en"),
                request.user.id,
            )
        elif ext == ".pdf":
            job = IngestionJob.objects.create(
                job_type="pdf", source_path=str(target),
                submitted_by=request.user,
            )
            ingest_pdf_task.delay(
                job.id, str(target), doc_code,
                s.validated_data.get("title", ""),
                s.validated_data.get("language", "en"),
                request.user.id,
            )
        else:
            return Response(
                {"detail": f"Unsupported file type: {ext}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record_event(
            "admin.doc_upload", actor=request.user,
            payload={"doc_code": doc_code, "file": upload.name, "job_id": job.id},
        )
        return Response(
            IngestionJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class IngestionJobView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request, job_id: int) -> Response:
        job = get_object_or_404(IngestionJob, id=job_id)
        return Response(IngestionJobSerializer(job).data)


class NodeListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request, doc_code: str) -> Response:
        doc = get_object_or_404(Document, doc_code=doc_code)
        if not doc.current_version_id:
            return Response({"results": []})
        nodes = doc.current_version.nodes.all().order_by("node_id")[:500]
        return Response({"results": NodeSerializer(nodes, many=True).data})


class NodeDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request, node_id: str) -> Response:
        node = get_object_or_404(
            Node, node_id=node_id, version__is_current=True,
        )
        return Response(NodeSerializer(node).data)

    def patch(self, request: Request, node_id: str) -> Response:
        node = get_object_or_404(
            Node, node_id=node_id, version__is_current=True,
        )
        s = NodeUpdateSerializer(node, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        # Re-embed asynchronously
        reembed_node_task.delay(node.id)
        record_event(
            "admin.node_edited", actor=request.user, target=node,
            payload={"node_id": node_id},
        )
        return Response(NodeSerializer(node).data)


class CitationAuditListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        status_filter = request.query_params.get("status", CitationAudit.PENDING)
        audits = CitationAudit.objects.filter(status=status_filter).order_by("-created_at")[:200]
        return Response({"results": CitationAuditSerializer(audits, many=True).data})


class CitationAuditResolveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request: Request, audit_id: int) -> Response:
        audit = get_object_or_404(CitationAudit, id=audit_id)
        s = CitationAuditResolveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        from django.utils import timezone

        audit.status = (
            CitationAudit.APPROVED if s.validated_data["decision"] == "approve"
            else CitationAudit.REJECTED
        )
        audit.resolution_notes = s.validated_data.get("notes", "")
        audit.resolved_by = request.user
        audit.resolved_at = timezone.now()
        audit.save()
        record_event(
            "admin.citation_resolved", actor=request.user, target=audit,
            payload={"decision": s.validated_data["decision"]},
        )
        return Response(CitationAuditSerializer(audit).data)


class SidebarView(APIView):
    """Returns the full sidebar payload for a clicked legal-basis row.

    Builds the 9-field payload defined in LLP_Answer_Reference_Guideline_v2.md.
    """
    permission_classes = []  # public (it's already proven to be in the answer)
    authentication_classes: list = []

    def get(self, request: Request, node_id: str) -> Response:
        node = get_object_or_404(Node, node_id=node_id, version__is_current=True)
        document = Document.objects.filter(doc_code=node.doc_code).first()

        # Amendment history: walk the supersession chain
        amendment_history: list[str] = []
        if document:
            cur: Document | None = document
            seen: set[int] = set()
            while cur and cur.id not in seen:
                seen.add(cur.id)
                year = cur.date_enacted.year if cur.date_enacted else "?"
                amendment_history.append(f"{cur.title} ({year})")
                cur = cur.amends if cur.amends_id else None

        # Related provisions: cross-references for this section
        related: list[str] = []
        if node.section_number:
            from .models import Crossref

            cr = (
                Crossref.objects.filter(
                    version__is_current=True,
                    section_number=node.section_number,
                ).first()
            )
            if cr:
                related = [f"Section {s}" for s in (cr.references or [])][:6]

        # Status + amendment label
        status_label = "Current operative version"
        if node.is_superseded:
            sb = node.supersession.get("superseded_by", {})
            sb_doc = sb.get("doc_id", "")
            status_label = f"Superseded by {sb_doc}" if sb_doc else "Superseded"

        provision_label = (
            f"Section {node.section_number}"
            if node.section_number
            else (f"Rule {node.rule_number}" if node.rule_number else node.title)
        )

        payload = {
            "authority_type": (document.instrument_type if document else "Act"),
            "instrument": (document.title if document else node.doc_code),
            "provision": provision_label,
            "status": status_label,
            "section_text": node.content or node.summary,
            "plain_language_explanation": node.summary,
            "amendment_history": amendment_history,
            "related_provisions": related,
            "why_it_matters": "",  # populated by chat layer at answer time
        }
        return Response(SidebarSerializer(payload).data)
