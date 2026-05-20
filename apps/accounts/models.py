"""User model and related domain types.

Roles map to Django Groups for permission scoping. The `role` field is a
denormalized convenience for the chat / subscriptions code.
"""
from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from typing import Any


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    STAFF = "staff", "Staff"
    PREMIUM = "premium_user", "Premium user"
    FREE = "free_user", "Free user"


class UserManager(BaseUserManager["User"]):
    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra: Any) -> "User":
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        extra.setdefault("is_email_verified", True)
        extra.setdefault("role", Role.ADMIN)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, max_length=254)    
    phone = models.CharField(max_length=20, blank=True)
    full_name = models.CharField(max_length=160, blank=True)
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.FREE)
    is_email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    preferred_language = models.CharField(max_length=8, default="en")
    external_id = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"
        indexes = [models.Index(fields=["role"])]

    def __str__(self) -> str:
        return self.email

    @property
    def is_premium(self) -> bool:
        return self.role in (Role.PREMIUM, Role.STAFF, Role.ADMIN)


class GuestSession(models.Model):
    """An anonymous device token used for free-tier guest quota tracking."""

    token = models.CharField(max_length=64, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    language = models.CharField(max_length=8, default="en")
    converted_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="guest_sessions"
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_guest_session"

    @classmethod
    def issue(cls, *, ip_address: str | None = None, user_agent: str = "", language: str = "en") -> "GuestSession":
        return cls.objects.create(
            token=secrets.token_urlsafe(32),
            ip_address=ip_address,
            user_agent=user_agent,
            language=language,
        )


class EmailVerificationToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="verification_tokens")
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_email_verification_token"

    @classmethod
    def issue(cls, user: User, hours: int = 24) -> "EmailVerificationToken":
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(hours=hours),
        )

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()


class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reset_tokens")
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_password_reset_token"

    @classmethod
    def issue(cls, user: User, hours: int = 1) -> "PasswordResetToken":
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(hours=hours),
        )

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
