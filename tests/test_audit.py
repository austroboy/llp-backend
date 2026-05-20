"""Audit log + hash-chain integrity tests."""
from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, verify_chain


@pytest.mark.django_db
def test_record_event_creates_row(user):
    ev = record_event("auth.login", actor=user, payload={"ip": "1.2.3.4"})
    assert ev is not None
    assert ev.event_type == "auth.login"
    assert ev.actor_id == user.id
    assert ev.hash
    assert len(ev.hash) == 64


@pytest.mark.django_db
def test_chain_verifies_after_multiple_events(user):
    """Recording several events keeps the chain consistent."""
    for i in range(5):
        record_event("auth.login", actor=user, payload={"i": i})

    result = verify_chain()
    assert result["ok"] is True
    assert result["checked"] == AuditEvent.objects.count()


@pytest.mark.django_db
def test_chain_links_events_via_prev_hash(user):
    """Each event's prev_hash equals the previous event's hash."""
    a = record_event("auth.login", actor=user)
    b = record_event("auth.logout", actor=user)
    assert b.prev_hash == a.hash


@pytest.mark.django_db
def test_chain_breaks_when_event_tampered(user):
    """Tampering with a row's payload breaks the chain."""
    record_event("auth.login", actor=user)
    ev = record_event("auth.logout", actor=user, payload={"good": True})
    record_event("auth.login", actor=user)

    # Tamper
    ev.payload = {"good": False}
    ev.save(update_fields=["payload"])

    result = verify_chain()
    assert result["ok"] is False
    assert result["broken_at_id"] == ev.id


@pytest.mark.django_db
def test_record_event_never_raises_on_failure(monkeypatch, user):
    """Audit failures don't break user requests — record_event must not raise."""
    monkeypatch.setattr(
        "apps.audit.models.AuditEvent.objects.order_by",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated DB error")),
    )
    # Should not raise even though the underlying call would
    result = record_event("auth.login", actor=user)
    assert result is None
