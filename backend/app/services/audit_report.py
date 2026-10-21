"""Audit report versions: a frozen snapshot of the audit work, and a sign-off that only applies to an unchanged snapshot."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditException, AuditReportVersion, Company, TrialBalanceImport, VouchingLink, utcnow
from .audit_service import AuditWorkflowError as WorkflowError
from .audit_log import log_event

OPEN = ("open", "needs_information", "under_review")


def build_snapshot(db: Session, company: Company) -> dict:
    exs = db.scalars(select(AuditException).where(AuditException.company_id == company.id).order_by(AuditException.id)).all()
    links = db.scalars(select(VouchingLink).where(VouchingLink.company_id == company.id)).all()
    imports = db.scalars(select(TrialBalanceImport).where(TrialBalanceImport.company_id == company.id)
                         .order_by(TrialBalanceImport.id)).all()
    return {
        "company": {"id": company.id, "name": company.name, "gstin": company.gstin},
        "imports": [{"id": i.id, "kind": i.kind, "period": i.period_label,
                     "period_start": str(i.period_start) if i.period_start else None,
                     "period_end": str(i.period_end) if i.period_end else None, "summary": i.summary} for i in imports],
        "exceptions": [{"id": x.id, "rule_id": x.rule_id, "severity": x.severity, "status": x.status, "title": x.title,
                        "explanation": x.explanation, "suggested_action": x.suggested_action} for x in exs],
        "open_exceptions": sum(1 for x in exs if x.status in OPEN),
        "open_high": sum(1 for x in exs if x.status in OPEN and x.severity == "high"),
        "vouching": {"linked": len(links), "discrepancies": sum(1 for l in links if l.outcome == "discrepancy"),
                     "pending_review": sum(1 for l in links if l.review_status == "pending")},
        "disclaimer": "FinV checks identify anomalies for the auditor's judgement. They are not a statutory audit.",
    }


def _hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def create_version(db: Session, company: Company, user_id: int, note: str | None) -> AuditReportVersion:
    snap = build_snapshot(db, company)
    last = db.scalar(select(func.max(AuditReportVersion.version)).where(AuditReportVersion.company_id == company.id)) or 0
    for old in db.scalars(select(AuditReportVersion).where(AuditReportVersion.company_id == company.id,
                                                          AuditReportVersion.status == "draft")):
        old.status = "superseded"                 # only the newest draft can be signed
    v = AuditReportVersion(company_id=company.id, version=last + 1, snapshot=snap, content_hash=_hash(snap),
                           note=(note or "").strip() or None, created_by=user_id)
    db.add(v)
    db.flush()
    log_event(db, company.id, user_id, "audit.report_version_created", "audit_report", v.id,
              {"version": v.version, "hash": v.content_hash, "open_exceptions": snap["open_exceptions"]})
    return v


def sign_off(db: Session, company: Company, v: AuditReportVersion, user_id: int, note: str | None, acknowledge_open: bool) -> AuditReportVersion:
    if v.status != "draft":
        raise WorkflowError("Only the newest draft can be signed off. This version is " + v.status.replace("_", " ") + ".", 409)
    if _hash(build_snapshot(db, company)) != v.content_hash:
        raise WorkflowError("The audit work has changed since this version was created. Create a new version and sign that one.", 409)
    open_n = v.snapshot.get("open_exceptions", 0)
    if open_n and not acknowledge_open:
        raise WorkflowError(f"{open_n} exception(s) are still open. Confirm that you are signing off with them open, "
                            "or resolve them and create a new version.", 409)
    v.status, v.signed_by, v.signed_at, v.sign_note = "signed_off", user_id, utcnow(), (note or "").strip() or None
    log_event(db, company.id, user_id, "audit.report_signed_off", "audit_report", v.id,
              {"version": v.version, "hash": v.content_hash, "open_exceptions": open_n, "acknowledged_open": bool(open_n)})
    return v
