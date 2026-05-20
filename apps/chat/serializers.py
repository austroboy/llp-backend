from rest_framework import serializers

from .models import ChatMessage, Conversation


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = (
            "id", "title", "language", "tier_at_start",
            "archived", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "tier_at_start")


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = (
            "id", "role", "content", "intent", "mode",
            "retrieved_node_ids", "legal_basis", "citations",
            "clarification_options", "cta", "next_step",
            "tokens_in", "tokens_out", "model_name",
            "latency_ms", "cached", "verdict", "created_at",
        )
        read_only_fields = fields


class SendMessageSerializer(serializers.Serializer):
    user_message = serializers.CharField(min_length=1, max_length=8000)
    language = serializers.ChoiceField(
        choices=("en", "bn"), required=False, default="en",
    )
    attachments = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list,
    )


class QuotaCheckRequestSerializer(serializers.Serializer):
    user_message_preview = serializers.CharField(
        max_length=200, required=False, allow_blank=True,
    )
