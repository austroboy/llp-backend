from django.contrib import admin

from .models import (
    CitationAudit,
    Crossref,
    Document,
    DocumentVersion,
    IngestionJob,
    KeywordIndex,
    Node,
    StatutorySynonym,
)


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    fk_name = "document"
    extra = 0
    readonly_fields = ("version", "ingested_at", "embedding_model", "is_current")
    fields = ("version", "embedding_model", "is_current", "ingested_at")
    show_change_link = True


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("doc_code", "title", "instrument_type", "language", "status", "is_parent")
    list_filter = ("status", "instrument_type", "language", "is_parent")
    search_fields = ("doc_code", "title")
    inlines = [DocumentVersionInline]
    raw_id_fields = ("amends", "superseded_by", "current_version")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document", "version", "is_current", "embedding_model", "ingested_at")
    list_filter = ("is_current", "embedding_model")
    raw_id_fields = ("document", "ingested_by")
    readonly_fields = ("created_at", "updated_at", "ingested_at")


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("node_id", "doc_code", "section_number", "rule_number", "language", "is_leaf")
    list_filter = ("doc_code", "language", "is_leaf")
    search_fields = ("node_id", "title", "section_number", "summary")
    raw_id_fields = ("version",)
    readonly_fields = ("search_vector",)


@admin.register(Crossref)
class CrossrefAdmin(admin.ModelAdmin):
    list_display = ("section_number", "ref_type", "version")
    raw_id_fields = ("version",)
    search_fields = ("section_number",)


@admin.register(KeywordIndex)
class KeywordIndexAdmin(admin.ModelAdmin):
    list_display = ("key", "doc_code")
    search_fields = ("key", "doc_code")


@admin.register(StatutorySynonym)
class StatutorySynonymAdmin(admin.ModelAdmin):
    list_display = ("language", "term")
    list_filter = ("language",)
    search_fields = ("term",)


@admin.register(IngestionJob)
class IngestionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "status", "document", "submitted_by", "created_at")
    list_filter = ("status", "job_type")
    raw_id_fields = ("document", "submitted_by")
    readonly_fields = ("started_at", "finished_at", "created_at", "updated_at")


@admin.register(CitationAudit)
class CitationAuditAdmin(admin.ModelAdmin):
    list_display = ("id", "raised_text", "expected_section", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("raised_text", "expected_section")
    raw_id_fields = ("resolved_by",)
