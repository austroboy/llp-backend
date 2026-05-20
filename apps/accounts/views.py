"""Authentication views."""
from __future__ import annotations

import logging

from django.contrib.auth import authenticate
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.services import record_event

from .models import (
    EmailVerificationToken,
    GuestSession,
    PasswordResetToken,
    User,
)
from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


def _issue_jwt(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user: User = s.save()

        # Send verification email
        token = EmailVerificationToken.issue(user)
        try:
            send_mail(
                subject="Verify your Labor Law Partner account",
                message=f"Please verify your email: {request.build_absolute_uri('/')}verify?token={token.token}",
                from_email=None,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning("verification email failed", extra={"user_id": user.id})

        record_event("auth.register", actor=user, payload={"email": user.email})
        return Response(
            {"user": UserSerializer(user).data, "tokens": _issue_jwt(user)},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(
            request=request,
            username=s.validated_data["email"],
            password=s.validated_data["password"],
        )
        if user is None or not user.is_active:
            return Response(
                {"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
            )
        user.last_login_at = timezone.now()
        user.save(update_fields=["last_login_at"])
        record_event("auth.login", actor=user)
        return Response({"user": UserSerializer(user).data, "tokens": _issue_jwt(user)})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        refresh = request.data.get("refresh")
        if refresh:
            try:
                token = RefreshToken(refresh)
                token.blacklist()
            except Exception:  # noqa: BLE001
                pass
        record_event("auth.logout", actor=request.user)
        return Response({"detail": "Logged out."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)

    def patch(self, request: Request) -> Response:
        s = UserSerializer(request.user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class GuestTokenView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR"))
        ua = request.META.get("HTTP_USER_AGENT", "")[:500]
        lang = request.data.get("language", "en")
        session = GuestSession.issue(ip_address=ip, user_agent=ua, language=lang)
        return Response({"guest_token": session.token, "language": session.language})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        s = PasswordResetRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            user = User.objects.get(email=s.validated_data["email"], is_active=True)
        except User.DoesNotExist:
            # Don't leak existence
            return Response({"detail": "If that email exists, a reset link was sent."})
        token = PasswordResetToken.issue(user)
        send_mail(
            subject="Reset your Labor Law Partner password",
            message=f"Reset link: {request.build_absolute_uri('/')}reset?token={token.token}",
            from_email=None,
            recipient_list=[user.email],
            fail_silently=True,
        )
        return Response({"detail": "If that email exists, a reset link was sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        s = PasswordResetConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            token = PasswordResetToken.objects.select_related("user").get(
                token=s.validated_data["token"]
            )
        except PasswordResetToken.DoesNotExist:
            return Response(
                {"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not token.is_valid():
            return Response(
                {"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST
            )
        token.user.set_password(s.validated_data["new_password"])
        token.user.save(update_fields=["password"])
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])
        record_event("auth.password_reset", actor=token.user)
        return Response({"detail": "Password reset successful."})


class EmailVerifyView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        token_str = request.data.get("token") or request.query_params.get("token")
        if not token_str:
            return Response({"detail": "Missing token."}, status=400)
        try:
            t = EmailVerificationToken.objects.select_related("user").get(token=token_str)
        except EmailVerificationToken.DoesNotExist:
            return Response({"detail": "Invalid token."}, status=400)
        if not t.is_valid():
            return Response({"detail": "Expired token."}, status=400)
        t.user.is_email_verified = True
        t.user.save(update_fields=["is_email_verified"])
        t.used_at = timezone.now()
        t.save(update_fields=["used_at"])
        record_event("auth.email_verified", actor=t.user)
        return Response({"detail": "Email verified."})
