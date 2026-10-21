"""Append-only, hash-chained audit trail.

Each event's hash covers its content and the previous event's hash for the same company, so any edit
or deletion of an earlier event breaks verification. (Someone with direct database write access could
rewrite the whole chain; exporting the latest hash periodically detects that.)
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditEvent, utcnow


def _digest(prev: str | None, company_id, user_id, action, object_type, object_id, details, created_at) -> str:
    body = json.dumps([prev, company_id, user_id, action, object_type, object_id, details,
                       created_at.isoformat()], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def log_event(db: Session, company_id: int | None, user_id: int | None, action: str,
              object_type: str | None = None, object_id: int | None = None, details: dict | None = None) -> AuditEvent:
    prev = db.scalar(select(AuditEvent.hash).where(AuditEvent.company_id == company_id)
                     .order_by(AuditEvent.id.desc()).limit(1)) if company_id is not None else None
    now = utcnow()
    details = json.loads(json.dumps(details or {}, default=str))
    ev = AuditEvent(company_id=company_id, user_id=user_id, action=action, object_type=object_type,
                    object_id=object_id, details=details, prev_hash=prev, created_at=now,
                    hash=_digest(prev, company_id, user_id, action, object_type, object_id, details, now))
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session, company_id: int) -> dict:
    events = db.scalars(select(AuditEvent).where(AuditEvent.company_id == company_id).order_by(AuditEvent.id)).all()
    prev = None
    for ev in events:
        expected = _digest(prev, ev.company_id, ev.user_id, ev.action, ev.object_type, ev.object_id, ev.details, ev.created_at)
        if ev.prev_hash != prev or ev.hash != expected:
            return {"valid": False, "events": len(events), "broken_at_event_id": ev.id}
        prev = ev.hash
    return {"valid": True, "events": len(events), "latest_hash": prev}
