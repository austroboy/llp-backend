"""Audit event recording. Records form a hash chain over prev_hash."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .models import AuditEvent

logger = logging.getLogger(__name__)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _compute_hash(event_type: str, actor_id: int | None, target_id: int | None,
                  target_type: str, payload: dict, prev_hash: str, ts: str) -> str:
    body = _canonical_json({
        "event_type": event_type,
        "actor_id": actor_id,
        "target_id": target_id,
        "target_type": target_type,
        "payload": payload,
        "prev_hash": prev_hash,
        "ts": ts,
    })
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def record_event(
    event_type: str,
    *,
    actor=None,
    target=None,
    target_type: str = "",
    payload: dict | None = None,
    request=None,
) -> AuditEvent:
    """Append an audit event. Always succeeds — failures are logged but not raised."""
    actor_id = actor.id if actor and getattr(actor, "is_authenticated", False) else None
    target_id = getattr(target, "id", None) if target else None
    if target and not target_type:
        target_type = target.__class__.__name__
    payload = payload or {}

    ip = ""
    ua = ""
    rid = ""
    if request is not None:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")) or ""
        ua = request.META.get("HTTP_USER_AGENT", "")[:1000]
        rid = getattr(request, "request_id", "")

    try:
        prev = AuditEvent.objects.order_by("-id").only("hash").first()
        prev_hash = prev.hash if prev and prev.hash else ""

        event = AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            ip_address=ip if ip else None,
            user_agent=ua,
            request_id=rid,
            prev_hash=prev_hash,
        )
        event.save()  # to get created_at
        event.hash = _compute_hash(
            event_type, actor_id, target_id, target_type,
            payload, prev_hash, event.created_at.isoformat(),
        )
        event.save(update_fields=["hash"])
        return event
    except Exception:  # noqa: BLE001
        logger.exception("audit_record_failed", extra={"event_type": event_type})
        # Never raise — audit failures should not break user requests
        return None  # type: ignore[return-value]


def verify_chain(limit: int = 1000) -> dict:
    """Check that the audit chain is intact. Returns {ok, broken_at}."""
    prev_hash = ""
    qs = AuditEvent.objects.order_by("id")[:limit]
    for ev in qs:
        expected = _compute_hash(
            ev.event_type, ev.actor_id, ev.target_id, ev.target_type,
            ev.payload, prev_hash, ev.created_at.isoformat(),
        )
        if ev.hash != expected or ev.prev_hash != prev_hash:
            return {"ok": False, "broken_at_id": ev.id, "expected": expected, "actual": ev.hash}
        prev_hash = ev.hash
    return {"ok": True, "checked": qs.count()}
