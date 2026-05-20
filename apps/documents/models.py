"""Document corpus models.

Maps 1:1 to the existing `llp-chat-data6.zip` shape:
  - universe-registry.json     → Document
  - DOC-XXX.json (full)        → DocumentVersion.raw_json
  - DOC-XXX.json (each leaf)   → Node
  - section-crossrefs.json     → Crossref
  - section-keyword-index.json → KeywordIndex
  - node-embeddings.json       → Node.embedding (768-dim pgvector)
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone
from pgvector.django import VectorField

from apps.common.models import TimestampedModel


class Document(TimestampedModel):
    """A legal instrument (Act, Amendment, Rules, Ordinance)."""

    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_SUPERSEDED = "superseded"
    STATUS_ACTIVE_WITH_AMENDMENTS = "active_with_amendments"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_ACTIVE_WITH_AMENDMENTS, "Active with amendments"),
    ]

    INSTRUMENT_TYPES = [
        ("Act", "Act"),
        ("Amendment Act", "Amendment Act"),
        ("Rules", "Rules"),
        ("Amendment Rules", "Amendment Rules"),
        ("Ordinance", "Ordinance"),
        ("Gazette", "Gazette"),
        ("SRO", "SRO"),
        ("Order", "Order"),
        ("Circular", "Circular"),
    ]

    doc_code = models.CharField(max_length=16, unique=True, db_index=True)
    title = models.TextField()
    instrument_type = models.CharField(max_length=48, choices=INSTRUMENT_TYPES)
    instrument_number = models.CharField(max_length=80, blank=True)
    date_enacted = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=8, default="en")
    is_parent = models.BooleanField(default=False)
    amends = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="amendments",
    )
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="supersedes_set",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    current_version = models.ForeignKey(
        "DocumentVersion", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "documents_document"
        ordering = ["doc_code"]

    def __str__(self) -> str:
        return f"{self.doc_code}: {self.title}"


class DocumentVersion(TimestampedModel):
    """A specific ingestion of a Document. Re-uploads create new versions."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="versions",
    )
    version = models.IntegerField()
    source_file = models.CharField(max_length=512, blank=True)
    raw_json = models.JSONField()  # full DOC-XXX.json — preserved for audit
    embedding_model = models.CharField(max_length=64, default="text-embedding-004")
    is_current = models.BooleanField(default=False, db_index=True)
    ingested_at = models.DateTimeField(default=timezone.now)
    ingested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "documents_document_version"
        unique_together = [("document", "version")]
        indexes = [
            models.Index(fields=["document", "is_current"]),
        ]


class Node(models.Model):
    """A leaf or branch unit of a legal document.

    Trees are flattened: each Node carries `parent_node_id`. Leaf nodes carry
    embeddings; branch nodes have summary text but typically no embedding
    (we still allow it for top-level retrieval).
    """

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="nodes"
    )
    node_id = models.CharField(max_length=48, db_index=True)
    parent_node_id = models.CharField(max_length=48, blank=True, db_index=True)
    doc_code = models.CharField(max_length=16, db_index=True)  # denormalized

    title = models.TextField(blank=True)
    start_index = models.IntegerField(null=True, blank=True)
    end_index = models.IntegerField(null=True, blank=True)
    language = models.CharField(max_length=8, default="en")
    summary = models.TextField(blank=True)
    content = models.TextField(blank=True)

    section_number = models.CharField(max_length=16, blank=True, db_index=True)
    rule_number = models.CharField(max_length=16, blank=True, db_index=True)

    supersession = models.JSONField(default=dict, blank=True)
    raw_node = models.JSONField(default=dict, blank=True)

    embedding = VectorField(dimensions=settings.EMBEDDING_DIM, null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    is_leaf = models.BooleanField(default=False, db_index=True)
    depth = models.IntegerField(default=0)

    class Meta:
        db_table = "documents_node"
        unique_together = [("version", "node_id")]
        indexes = [
            GinIndex(fields=["search_vector"]),
            models.Index(fields=["doc_code", "section_number"]),
        ]

    def __str__(self) -> str:
        return self.node_id

    @property
    def is_superseded(self) -> bool:
        return self.supersession.get("status") == "superseded"


class Crossref(models.Model):
    """Section-level cross-reference data."""

    REF_TYPES = [("section", "Section"), ("rule", "Rule")]

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="crossrefs"
    )
    section_number = models.CharField(max_length=16, db_index=True)
    ref_type = models.CharField(max_length=8, choices=REF_TYPES, default="section")
    node_ids = ArrayField(models.CharField(max_length=48), default=list)
    references = ArrayField(models.CharField(max_length=16), default=list)
    referenced_by = ArrayField(models.CharField(max_length=16), default=list)

    class Meta:
        db_table = "documents_crossref"
        unique_together = [("version", "section_number", "ref_type")]


class KeywordIndex(models.Model):
    """Per-section keyword index."""

    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, related_name="keyword_indexes"
    )
    key = models.CharField(max_length=80, db_index=True)
    doc_code = models.CharField(max_length=16, blank=True)
    prior_doc_code = models.CharField(max_length=16, blank=True)
    keywords = ArrayField(models.CharField(max_length=80), default=list)

    class Meta:
        db_table = "documents_keyword_index"


class StatutorySynonym(models.Model):
    """Bilingual synonym pairs (bn-statutory-synonyms.json + en-statutory-synonyms.json)."""

    language = models.CharField(max_length=8)  # 'bn'|'en'
    term = models.CharField(max_length=200, db_index=True)
    synonyms = ArrayField(models.CharField(max_length=200), default=list)

    class Meta:
        db_table = "documents_statutory_synonym"
        unique_together = [("language", "term")]


class IngestionJob(TimestampedModel):
    """Tracks Celery ingestion progress, exposed through the admin UI."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_FAILED, "Failed"),
    ]

    JOB_TYPES = [
        ("docx", ".docx upload"),
        ("pdf", ".pdf upload"),
        ("json", "JSON upload"),
        ("bulk_zip", "Bulk zip import"),
    ]

    job_type = models.CharField(max_length=16, choices=JOB_TYPES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ingestion_jobs",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    source_path = models.CharField(max_length=512)
    progress = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents_ingestion_job"


class CitationAudit(TimestampedModel):
    """Pending review of a citation that didn't match a known node."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    raised_text = models.CharField(max_length=200)
    expected_section = models.CharField(max_length=16, blank=True)
    chat_message_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="resolved_citation_audits",
    )
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents_citation_audit"
        indexes = [models.Index(fields=["status", "-created_at"])]
