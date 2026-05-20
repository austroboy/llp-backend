from rest_framework import serializers

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = AuditEvent
        fields = (
            "id", "event_type", "actor_id", "actor_email",
            "target_type", "target_id", "payload",
            "ip_address", "request_id", "created_at",
        )
