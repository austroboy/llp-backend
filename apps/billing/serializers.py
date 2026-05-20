from rest_framework import serializers

from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = (
            "id", "target_tier", "amount_bdt", "status",
            "provider", "provider_ref", "paid_at",
            "created_at", "updated_at",
        )
