"""Audit workspace services: imports, checks, exceptions and vouching.

Audit review never modifies accounting records. Corrections go through the Accounting approval workflow.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import audit_checks as ac
from ..engine.normalize import fingerprint
from ..models import (AuditException, Company, CompanySettings, ExceptionComment, LedgerEntry, SourceRecord,
                      TrialBalanceImport, TrialBalanceLine, VouchingLink, utcnow)
from .audit_log import log_event

EXCEPTION_STATUSES = ["open", "needs_information", "under_review", "resolved", "accepted_with_explanation"]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class AuditWorkflowError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message, self.status = message, status


def store_import(db: Session, company: Company, doc, ex, period_label, period_start, period_end) -> TrialBalanceImport:
    imp = TrialBalanceImport(company_id=company.id, document_id=doc.id, kind=ex.kind, period_label=period_label,
                             period_start=period_start, period_end=period_end)
    db.add(imp)
    db.flush()
    for l in ex.tb_lines:
        db.add(TrialBalanceLine(import_id=imp.id, row_index=l["row_index"], account_code=l["account_code"],
                                account_name=l["account_name"], account_type=l["account_type"], opening=l["opening"],
                                debit=l["debit"], credit=l["credit"], closing=l["closing"]))
    for l in ex.ledger_lines:
        db.add(LedgerEntry(company_id=company.id, import_id=imp.id, row_index=l["row_index"], entry_date=l["entry_date"],
                           account_code=l["account_code"], account_name=l["account_name"], voucher_no=l["voucher_no"],
                           voucher_type=l["voucher_type"], party=l["party"], reference=l["reference"],
                           narration=l["narration"], debit=l["debit"], credit=l["credit"]))
    db.flush()
    run_checks(db, company, imp, None)
    return imp


def _settings(db: Session, company_id: int) -> CompanySettings:
    return db.get(CompanySettings, company_id) or CompanySettings(company_id=company_id)


def _upsert(db: Session, company_id: int, import_id: int | None, f: ac.Finding) -> tuple[AuditException, bool]:
    fp = fingerprint(import_id, f.rule_id, f.key or f.object_id)
    ex = db.scalar(select(AuditException).where(AuditException.company_id == company_id, AuditException.fingerprint == fp))
    if ex:
        ex.explanation, ex.evidence = f.explanation, f.evidence  # refresh evidence; keep status & history
        return ex, False
    ex = AuditException(company_id=company_id, import_id=import_id, object_type=f.object_type, object_id=f.object_id,
                        rule_id=f.rule_id, severity=f.severity, title=f.title[:255], explanation=f.explanation,
                        evidence=f.evidence, suggested_action=f.action, fingerprint=fp)
    db.add(ex)
    return ex, True


def run_checks(db: Session, company: Company, imp: TrialBalanceImport, user_id: int | None) -> dict:
    s = _settings(db, company.id)
    materiality = s.materiality_amount or Decimal("0")
    findings: list[ac.Finding] = []
    summary: dict = {}
    if imp.kind == "trial_balance":
        lines = db.scalars(select(TrialBalanceLine).where(TrialBalanceLine.import_id == imp.id)
                           .order_by(TrialBalanceLine.row_index)).all()
        summary, findings = ac.check_trial_balance(
            [ac.TBLine(l.id, l.row_index, l.account_code, l.account_name, l.account_type, l.opening, l.debit, l.credit,
                       l.closing) for l in lines], materiality)
    else:
        lines = db.scalars(select(LedgerEntry).where(LedgerEntry.import_id == imp.id).order_by(LedgerEntry.row_index)).all()
        ll = [_ledger_line(l) for l in lines]
        tb = db.scalar(select(TrialBalanceImport).where(
            TrialBalanceImport.company_id == company.id, TrialBalanceImport.kind == "trial_balance",
            TrialBalanceImport.period_start == imp.period_start, TrialBalanceImport.period_end == imp.period_end)
            .order_by(TrialBalanceImport.id.desc())) if imp.period_start else None
        movements = None
        if tb:
            movements = {}
            for t in db.scalars(select(TrialBalanceLine).where(TrialBalanceLine.import_id == tb.id)).all():
                movements[(t.account_code or t.account_name or "").strip().upper()] = (t.debit, t.credit)
        findings = ac.check_ledger(ll, materiality, (imp.period_start, imp.period_end), movements)
        linked = {v.ledger_entry_id for v in db.scalars(select(VouchingLink).where(VouchingLink.company_id == company.id)).all()}
        findings += ac.missing_evidence(ll, linked, s.vouching_threshold or Decimal("0"))
        summary = {"entries": len(ll), "total_debit": str(sum((l.debit for l in ll), Decimal("0"))),
                   "total_credit": str(sum((l.credit for l in ll), Decimal("0"))),
                   "compared_with_trial_balance": tb.id if tb else None}
    new = 0
    for f in findings:
        _, created = _upsert(db, company.id, imp.id, f)
        new += created
    # missing-evidence exceptions that are now evidenced are resolved automatically, with history
    if imp.kind == "ledger":
        current = {fingerprint(imp.id, f.rule_id, f.key) for f in findings if f.rule_id == ac.R_VCH_MISSING.id}
        for ex in db.scalars(select(AuditException).where(AuditException.import_id == imp.id,
                                                          AuditException.rule_id == ac.R_VCH_MISSING.id,
                                                          AuditException.status == "open")).all():
            if ex.fingerprint not in current:
                ex.status = "resolved"
                db.add(ExceptionComment(exception_id=ex.id, user_id=user_id, from_status="open", to_status="resolved",
                                        comment="Evidence was linked; resolved automatically by FinV."))
    imp.summary = {**summary, "findings": len(findings), "new_exceptions": new, "checked_at": utcnow().isoformat()}
    db.flush()
    if user_id:
        log_event(db, company.id, user_id, "audit.checks_run", "tb_import", imp.id, {"findings": len(findings), "new": new})
    return imp.summary


def _ledger_line(l: LedgerEntry) -> ac.LedgerLine:
    return ac.LedgerLine(l.id, l.row_index, l.entry_date, l.account_code, l.account_name, l.voucher_no, l.party,
                         l.reference, l.narration, l.debit, l.credit)


def link_evidence(db: Session, company: Company, entry: LedgerEntry, record: SourceRecord, user_id: int) -> VouchingLink:
    if record.company_id != company.id or entry.company_id != company.id:
        raise AuditWorkflowError("Evidence must belong to the same company.", 404)
    taxes = sum(((record.cgst or 0), (record.sgst or 0), (record.igst or 0)), Decimal("0"))
    amount = record.total_amount
    if record.record_type == "bank_txn":
        amount = record.debit_amount or record.credit_amount
    ev = ac.Evidence(record.id, record.record_type, record.party_name, record.party_gstin, record.doc_number,
                     record.doc_date, amount, record.reference, taxes, record.taxable_amount, record.description)
    res = ac.vouch(_ledger_line(entry), ev)
    reuse = db.scalars(select(VouchingLink).where(VouchingLink.company_id == company.id, VouchingLink.record_id == record.id,
                                                  VouchingLink.ledger_entry_id != entry.id)).all()
    if reuse:
        res["checks"].append({"check": "evidence_reuse", "ok": False,
                              "detail": f"This document is already linked to {len(reuse)} other ledger entr{'y' if len(reuse) == 1 else 'ies'}."})
        res["outcome"] = "discrepancy"
        res["summary"] += " The same document supports another entry (possible duplicate voucher)."
    link = VouchingLink(company_id=company.id, ledger_entry_id=entry.id, record_id=record.id, result=res,
                        outcome=res["outcome"], created_by=user_id)
    db.add(link)
    db.flush()
    if res["outcome"] == "discrepancy":
        failed = [c for c in res["checks"] if c["ok"] is False]
        f = ac.Finding(ac.R_VCH_DIFF.id if any(c["check"] != "evidence_reuse" for c in failed) else ac.R_VCH_REUSE.id,
                       "medium", f"Vouching discrepancy: {entry.account_name or entry.account_code} {entry.voucher_no or ''}".strip(),
                       res["summary"] + " " + "; ".join(c["detail"] for c in failed), "ledger_entry", entry.id,
                       {"vouching_link_id": link.id, "checks": res["checks"]},
                       "Obtain an explanation or the correct supporting document.", f"vch:{link.id}")
        _upsert(db, company.id, entry.import_id, f)
    log_event(db, company.id, user_id, "audit.evidence_linked", "ledger_entry", entry.id,
              {"record_id": record.id, "outcome": res["outcome"]})
    imp = db.get(TrialBalanceImport, entry.import_id)
    run_checks(db, company, imp, user_id)
    return link


def review_link(db: Session, company: Company, link: VouchingLink, user_id: int, status: str, comment: str | None) -> VouchingLink:
    if status not in {"agreed", "queried", "pending"}:
        raise AuditWorkflowError("Status must be agreed, queried or pending.", 422)
    link.review_status = status
    link.comment = comment
    log_event(db, company.id, user_id, "audit.vouching_reviewed", "vouching_link", link.id, {"status": status, "comment": comment})
    db.flush()
    return link


def update_exception(db: Session, company: Company, ex: AuditException, user_id: int, status: str | None,
                     comment: str | None, assignee_id: int | None) -> AuditException:
    if status and status not in EXCEPTION_STATUSES:
        raise AuditWorkflowError("Unknown exception status.", 422)
    if status in {"resolved", "accepted_with_explanation"} and not (comment and comment.strip()):
        raise AuditWorkflowError("Explain the resolution in a comment.", 422)
    if not status and not comment and assignee_id is None:
        raise AuditWorkflowError("Nothing to update.", 422)
    old = ex.status
    if status:
        ex.status = status
    if assignee_id is not None:
        ex.assignee_id = assignee_id
    db.add(ExceptionComment(exception_id=ex.id, user_id=user_id, from_status=old if status else None,
                            to_status=status, comment=comment))
    log_event(db, company.id, user_id, "audit.exception_updated", "audit_exception", ex.id,
              {"from": old, "to": status, "comment": comment, "assignee_id": assignee_id})
    db.flush()
    return ex
