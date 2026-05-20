from django.contrib import admin
from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import QuotaCounter, SubscriptionEvent, TierConfig, UserSubscription


@admin.register(TierConfig)
class TierConfigAdmin(admin.ModelAdmin):
    list_display = ("tier", "label", "daily_request_limit", "price_bdt", "is_active")
    list_filter = ("is_active",)
    search_fields = ("tier", "label")
    fieldsets = (
        ("Identity", {"fields": ("tier", "label", "label_bn", "is_active")}),
        ("Limits", {"fields": (
            "daily_request_limit", "rate_limit_per_min",
            "session_response_cap", "memory_window_days", "zone2_max_rows",
        )}),
        ("Capabilities", {"fields": (
            "allowed_intents", "file_upload_allowed",
            "cross_domain_allowed", "advisory_allowed",
        )}),
        ("Pricing", {"fields": ("price_bdt",)}),
    )


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "starts_at", "expires_at", "auto_renew")
    list_filter = ("tier", "status", "auto_renew")
    search_fields = ("user__email", "payment_ref")
    raw_id_fields = ("user", "granted_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(QuotaCounter)
class QuotaCounterAdmin(admin.ModelAdmin):
    list_display = ("user", "guest_token", "tier", "date", "used")
    list_filter = ("date", "tier")
    search_fields = ("user__email", "guest_token")
    raw_id_fields = ("user",)


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "from_tier", "to_tier", "created_at")
    list_filter = ("event_type", "to_tier")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)
    readonly_fields = ("created_at",)


@receiver(post_save, sender=TierConfig)
def invalidate_tier_cache(sender, instance: TierConfig, **kwargs) -> None:
    cache.delete(f"tier_config:{instance.tier}")
