from django.contrib import admin

from .models import ChatMessage, Conversation, FileAttachment, ResponseCache


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = (
        "role", "content", "intent", "mode", "tokens_in", "tokens_out",
        "verdict", "created_at",
    )
    fields = readonly_fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "guest_token", "title", "tier_at_start",
                    "archived", "updated_at")
    list_filter = ("archived", "tier_at_start", "language")
    search_fields = ("title", "user__email", "guest_token")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "intent", "mode",
                    "tokens_out", "verdict", "created_at")
    list_filter = ("role", "intent", "mode", "verdict", "cached")
    search_fields = ("content", "prompt_hash", "conversation__id")
    readonly_fields = ("created_at",)


@admin.register(ResponseCache)
class ResponseCacheAdmin(admin.ModelAdmin):
    list_display = ("query_hash", "tier", "language", "hits", "expires_at")
    list_filter = ("tier", "language")
    search_fields = ("query_hash",)
    readonly_fields = ("created_at",)


@admin.register(FileAttachment)
class FileAttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "filename", "size_bytes", "created_at")
    search_fields = ("filename", "conversation__id")
    readonly_fields = ("created_at",)
