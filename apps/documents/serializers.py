from rest_framework import serializers

from .models import (
    CitationAudit,
    Crossref,
    Document,
    DocumentVersion,
    IngestionJob,
    Node,
)


class DocumentSerializer(serializers.ModelSerializer):
    versions_count = serializers.IntegerField(source="versions.count", read_only=True)

    class Meta:
        model = Document
        fields = (
            "id", "doc_code", "title", "instrument_type", "instrument_number",
            "date_enacted", "language", "is_parent", "status", "metadata",
            "versions_count", "created_at", "updated_at",
        )
        read_only_fields = ("id", "versions_count", "created_at", "updated_at")


class DocumentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentVersion
        fields = (
            "id", "version", "source_file", "embedding_model",
            "is_current", "ingested_at", "notes",
        )


class NodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Node
        fields = (
            "id", "node_id", "parent_node_id", "doc_code", "title",
            "language", "summary", "content", "section_number", "rule_number",
            "supersession", "is_leaf", "depth",
        )


class NodeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Node
        fields = ("title", "summary", "content")


class IngestionJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = IngestionJob
        fields = (
            "id", "job_type", "status", "document", "source_path",
            "progress", "error_message",
            "started_at", "finished_at", "created_at",
        )


class CitationAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = CitationAudit
        fields = (
            "id", "raised_text", "expected_section",
            "chat_message_id", "status", "resolution_notes",
            "created_at", "resolved_at",
        )


class DocumentUploadSerializer(serializers.Serializer):
    doc_code = serializers.RegexField(regex=r"^DOC-\d{3}$")
    file = serializers.FileField()
    title = serializers.CharField(max_length=300, required=False, allow_blank=True)
    instrument_type = serializers.CharField(max_length=48, required=False, default="Act")
    language = serializers.ChoiceField(choices=("en", "bn"), default="en")


class CitationAuditResolveSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=("approve", "reject"))
    notes = serializers.CharField(required=False, allow_blank=True)


class SidebarSerializer(serializers.Serializer):
    """Sidebar payload returned by /chat/sidebar/{node_id}/."""

    authority_type = serializers.CharField()
    instrument = serializers.CharField()
    provision = serializers.CharField()
    status = serializers.CharField()
    section_text = serializers.CharField()
    plain_language_explanation = serializers.CharField(allow_blank=True)
    amendment_history = serializers.ListField(child=serializers.CharField())
    related_provisions = serializers.ListField(child=serializers.CharField())
    why_it_matters = serializers.CharField(allow_blank=True)
