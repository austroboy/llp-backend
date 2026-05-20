from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import EmailVerificationToken, GuestSession, PasswordResetToken, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("-created_at",)
    list_display = ("email", "full_name", "role", "is_email_verified", "is_active", "created_at")
    list_filter = ("role", "is_active", "is_email_verified", "is_staff")
    search_fields = ("email", "full_name", "phone", "external_id")
    readonly_fields = ("created_at", "last_login_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("full_name", "phone", "preferred_language")}),
        ("Status", {"fields": ("role", "is_active", "is_email_verified", "is_staff", "is_superuser")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
        ("Metadata", {"fields": ("metadata", "external_id", "created_at", "last_login_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "role"),
        }),
    )


@admin.register(GuestSession)
class GuestSessionAdmin(admin.ModelAdmin):
    list_display = ("token", "ip_address", "language", "created_at", "last_seen_at")
    search_fields = ("token", "ip_address")
    readonly_fields = ("token", "created_at", "last_seen_at")


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at")
    raw_id_fields = ("user",)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at")
    raw_id_fields = ("user",)
