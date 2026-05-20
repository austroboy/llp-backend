from django.contrib import admin

from .models import Invoice, WebhookEvent


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "target_tier", "amount_bdt", "status",
                    "provider", "paid_at", "created_at")
    list_filter = ("status", "provider", "target_tier")
    search_fields = ("user__email", "provider_ref", "provider_session_id")
    raw_id_fields = ("user", "subscription")
    readonly_fields = ("created_at", "updated_at", "paid_at")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "event_type", "event_id",
                    "processed", "received_at")
    list_filter = ("provider", "event_type", "processed")
    search_fields = ("event_id",)
    readonly_fields = ("received_at", "processed_at")
