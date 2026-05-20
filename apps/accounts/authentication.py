"""Custom DRF authentication for anonymous guest users via X-Guest-Token header."""
from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from .models import GuestSession


class GuestUser(AnonymousUser):
    """Anonymous user enriched with a GuestSession reference."""

    def __init__(self, session: GuestSession):
        self.guest_session = session

    @property
    def is_authenticated(self) -> bool:  # type: ignore[override]
        # Still considered anonymous for permission purposes;
        # views should look at request.guest_session for tier resolution.
        return False

    @property
    def guest_token(self) -> str:
        return self.guest_session.token


class GuestTokenAuthentication(BaseAuthentication):
    """If a request carries `X-Guest-Token: <token>`, attach the matching GuestSession."""

    def authenticate(self, request: Request):
        token = request.headers.get("X-Guest-Token")
        if not token:
            return None
        try:
            session = GuestSession.objects.get(token=token)
        except GuestSession.DoesNotExist:
            return None
        # Don't override real auth — return None so JWT auth still wins if present
        request.guest_session = session  # type: ignore[attr-defined]
        return None
