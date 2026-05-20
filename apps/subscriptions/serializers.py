from rest_framework import serializers

from .models import TierConfig, UserSubscription


class TierConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = TierConfig
        fields = (
            "tier", "label", "label_bn",
            "daily_request_limit", "rate_limit_per_min",
            "allowed_intents", "file_upload_allowed",
            "cross_domain_allowed", "advisory_allowed",
            "memory_window_days", "zone2_max_rows", "price_bdt",
        )


class UserSubscriptionSerializer(serializers.ModelSerializer):
    is_currently_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserSubscription
        fields = (
            "id", "tier", "status", "starts_at", "expires_at",
            "auto_renew", "is_currently_active", "created_at",
        )
        read_only_fields = fields


class UpgradeRequestSerializer(serializers.Serializer):
    target_tier = serializers.ChoiceField(choices=("mini", "max"))
    payment_provider = serializers.ChoiceField(
        choices=("stripe", "sslcommerz", "manual"),
        default="stripe",
    )
