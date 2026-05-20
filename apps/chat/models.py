"""Chat conversation + message models."""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.common.models import TimestampedModel


class Conversation(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )
    guest_token = models.CharField(max_length=64, blank=True, db_index=True)
    title = models.CharField(max_length=200, blank=True)
    language = models.CharField(max_length=8, default="en")
    tier_at_start = models.CharField(max_length=32, blank=True)
    summary = models.TextField(blank=True)  # rolling summary of older turns
    archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "chat_conversation"
        indexes = [
            models.Index(fields=["user", "-updated_at"]),
            models.Index(fields=["guest_token", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return self.title or f"Conversation #{self.id}"


class ChatMessage(models.Model):
    """A single turn (user, assistant, or system note)."""

    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"
    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
        (ROLE_SYSTEM, "System"),
    ]

    MODE_DIRECT = "direct"
    MODE_SITUATION = "situation"
    MODE_DOCUMENT_REVIEW = "document_review"
    MODE_CLARIFICATION = "clarification"
    MODE_CHOICES = [
        (MODE_DIRECT, "Direct question"),
        (MODE_SITUATION, "Situation"),
        (MODE_DOCUMENT_REVIEW, "Document review"),
        (MODE_CLARIFICATION, "Clarification"),
    ]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    intent = models.CharField(max_length=32, blank=True)
    mode = models.CharField(max_length=24, choices=MODE_CHOICES, blank=True)

    # Retrieval evidence
    retrieved_node_ids = ArrayField(
        models.CharField(max_length=48), default=list, blank=True,
    )
    legal_basis = models.JSONField(default=list, blank=True)  # [{issue, reference_label, node_id}]
    citations = models.JSONField(default=list, blank=True)
    clarification_options = models.JSONField(default=list, blank=True)
    cta = models.JSONField(default=dict, blank=True)
    next_step = models.CharField(max_length=400, blank=True)
    attachments = models.JSONField(default=list, blank=True)

    # Cost / observability
    prompt_hash = models.CharField(max_length=64, blank=True, db_index=True)
    tokens_in = models.IntegerField(default=0)
    tokens_out = models.IntegerField(default=0)
    model_name = models.CharField(max_length=64, blank=True)
    latency_ms = models.IntegerField(default=0)
    cached = models.BooleanField(default=False)
    verdict = models.CharField(max_length=24, blank=True)  # 'verified'|'partial'|'unverified'

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "chat_message"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]


class ResponseCache(models.Model):
    """Persisted response cache.

    Redis is the live cache for hot lookups; this row is written for
    durability and to survive Redis evictions.
    """

    query_hash = models.CharField(max_length=64, primary_key=True)
    tier = models.CharField(max_length=32)
    language = models.CharField(max_length=8)
    payload = models.JSONField()
    hits = models.IntegerField(default=0)
    expires_at = models.DateTimeField()
    corpus_version = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_response_cache"


class FileAttachment(models.Model):
    """User-uploaded file attached to a message (Max tier)."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="attachments_set",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    filename = models.CharField(max_length=300)
    content_type = models.CharField(max_length=100)
    size_bytes = models.BigIntegerField()
    storage_path = models.CharField(max_length=500)
    extracted_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_file_attachment"
