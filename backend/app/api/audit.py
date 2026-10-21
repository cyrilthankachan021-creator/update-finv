from __future__ import annotations

import csv
import io

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (Account, JournalEntry, JournalLine, AuditReportVersion, AuditEvent, AuditException, ExceptionComment, LedgerEntry, SourceRecord, TrialBalanceImport,
                      TrialBalanceLine, User, VouchingLink)
from ..security import CompanyAccess, company_access
from ..services import audit_report as report_svc
from ..services.audit_log import log_event
from ..services import audit_service as svc
from ..services import tally_pull as tally_pull_svc
from . import serializers as S

router = APIRouter(prefix="/api/companies/{company_id}/audit", tags=["audit"])


def _get(db, model, obj_id, company_id, label):
    o = db.get(model, obj_id)
    if o is None or o.company_id != company_id:
        raise HTTPException(404, f"{label} not found.")
    return o


def _wf(fn, *a):
    try:
        return fn(*a)
    except svc.AuditWorkflowError as exc:
        raise HTTPException(exc.status, exc.message)


@router.get("/overview")
def overview(import_id: int | None = None, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    cid = acc.company.id
    imports = db.scalars(select(TrialBalanceImport).where(TrialBalanceImport.company_id == cid)
                         .order_by(TrialBalanceImport.id.desc())).all()
    exq = select(AuditException.status, func.count()).where(AuditException.company_id == cid)
    if import_id:
        exq = exq.where(AuditException.import_id == import_id)
    by_status = dict(db.execute(exq.group_by(AuditException.status)).all())
    sev = dict(db.execute(select(AuditException.severity, func.count()).where(
        AuditException.company_id == cid, AuditException.status.in_(["open", "needs_information", "under_review"]))
        .group_by(AuditException.severity)).all())
    links = db.scalars(select(VouchingLink).where(VouchingLink.company_id == cid)).all()
    users = {u.id: u.name for u in db.scalars(select(User))}
    recent = db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action.like("audit.%"))
                        .order_by(AuditEvent.id.desc()).limit(10)).all()
    return {"imports": [S.tb_import(i) for i in imports],
            "exceptions_by_status": by_status, "open_by_severity": sev,
            "awaiting_review": by_status.get("open", 0) + by_status.get("under_review", 0) + by_status.get("needs_information", 0),
            "vouching": {"linked": len(links), "discrepancies": sum(1 for l in links if l.outcome == "discrepancy"),
                         "pending_review": sum(1 for l in links if l.review_status == "pending")},
            "recent_activity": [S.event(e, users) for e in recent]}


@router.get("/imports/{import_id}")
def get_import(import_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    imp = _get(db, TrialBalanceImport, import_id, acc.company.id, "Import")
    out = S.tb_import(imp)
    if imp.kind == "trial_balance":
        lines = db.scalars(select(TrialBalanceLine).where(TrialBalanceLine.import_id == imp.id).order_by(TrialBalanceLine.row_index)).all()
        out["lines"] = [{"id": l.id, "row_index": l.row_index, "account_code": l.account_code, "account_name": l.account_name,
                         "account_type": l.account_type, "opening": S.m(l.opening), "debit": S.m(l.debit),
                         "credit": S.m(l.credit), "closing": S.m(l.closing)} for l in lines]
    exs = db.scalars(select(AuditException).where(AuditException.import_id == imp.id)).all()
    out["exceptions"] = sorted([S.exception(x) for x in exs], key=lambda x: (svc.SEVERITY_ORDER.get(x["severity"], 9), x["id"]))
    return out


@router.post("/imports/{import_id}/run-checks")
def run_checks(import_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    imp = _get(db, TrialBalanceImport, import_id, acc.company.id, "Import")
    res = svc.run_checks(db, acc.company, imp, acc.user.id)
    db.commit()
    return res


@router.get("/ledger")
def ledger(import_id: int | None = None, account: str | None = None, q: str | None = None, unvouched: bool = False,
           date_from: date | None = Query(default=None, alias="from"), date_to: date | None = Query(default=None, alias="to"),
           sort: str = Query(default="date", pattern=r"^(date|amount|voucher|account)$"), direction: str = Query(default="asc", alias="dir", pattern=r"^(asc|desc)$"),
           page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
           acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(LedgerEntry).where(LedgerEntry.company_id == acc.company.id)
    if import_id:
        stmt = stmt.where(LedgerEntry.import_id == import_id)
    if account:
        stmt = stmt.where(or_(LedgerEntry.account_code == account, LedgerEntry.account_name == account))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(LedgerEntry.narration.ilike(like), LedgerEntry.voucher_no.ilike(like),
                              LedgerEntry.party.ilike(like), LedgerEntry.account_name.ilike(like)))
    if date_from:
        stmt = stmt.where(LedgerEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(LedgerEntry.entry_date <= date_to)
    linked = select(VouchingLink.ledger_entry_id).where(VouchingLink.company_id == acc.company.id)
    if unvouched:
        stmt = stmt.where(LedgerEntry.id.notin_(linked))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    key = {"date": LedgerEntry.entry_date, "amount": LedgerEntry.debit + LedgerEntry.credit, "voucher": LedgerEntry.voucher_no,
           "account": LedgerEntry.account_name}[sort]
    order = key.desc() if direction == "desc" else key.asc()
    rows = db.scalars(stmt.order_by(order, LedgerEntry.row_index).offset((page - 1) * page_size).limit(page_size)).all()
    sub = stmt.subquery()
    dr_total, cr_total = db.execute(select(func.coalesce(func.sum(sub.c.debit), 0), func.coalesce(func.sum(sub.c.credit), 0))).one()
    balances = None
    if account:
        tb = db.scalar(select(TrialBalanceImport).where(TrialBalanceImport.company_id == acc.company.id, TrialBalanceImport.kind == "trial_balance")
                       .order_by(TrialBalanceImport.id.desc()))
        line = db.scalar(select(TrialBalanceLine).where(TrialBalanceLine.import_id == tb.id, or_(TrialBalanceLine.account_code == account,
                                                                                               TrialBalanceLine.account_name == account))) if tb else None
        balances = {"opening": S.m(line.opening) if line else None, "closing": S.m(line.closing) if line else None,
                    "from_trial_balance": tb.id if line else None,
                    "note": None if line and (line.opening is not None or line.closing is not None) else
                    "No opening or closing balance is available for this account: import a trial balance that includes it."}
    link_map: dict[int, list] = {}
    for v in db.scalars(select(VouchingLink).where(VouchingLink.ledger_entry_id.in_([r.id for r in rows]))):
        link_map.setdefault(v.ledger_entry_id, []).append(S.vouching(v))
    accounts = db.execute(select(LedgerEntry.account_code, LedgerEntry.account_name, func.count(),
                                 func.count(LedgerEntry.id)).where(LedgerEntry.company_id == acc.company.id)
                          .group_by(LedgerEntry.account_code, LedgerEntry.account_name)).all()
    return {"total": total, "page": page, "page_size": page_size,
            "totals": {"debit": S.m(Decimal(dr_total)), "credit": S.m(Decimal(cr_total))}, "balances": balances,
            "items": [{**S.ledger_entry(r), "vouching": link_map.get(r.id, [])} for r in rows],
            "accounts": [{"code": a[0], "name": a[1], "entries": a[2]} for a in accounts]}


@router.get("/ledger/{entry_id}")
def ledger_entry(entry_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    l = _get(db, LedgerEntry, entry_id, acc.company.id, "Ledger entry")
    links = db.scalars(select(VouchingLink).where(VouchingLink.ledger_entry_id == l.id)).all()
    recs = {r.id: S.record(r, brief=True) for r in db.scalars(select(SourceRecord).where(SourceRecord.id.in_([v.record_id for v in links])))}
    exs = db.scalars(select(AuditException).where(AuditException.company_id == acc.company.id,
                                                  AuditException.object_type == "ledger_entry",
                                                  AuditException.object_id == l.id)).all()
    return {**S.ledger_entry(l), "vouching": [{**S.vouching(v), "record": recs.get(v.record_id)} for v in links],
            "exceptions": [S.exception(x) for x in exs]}


@router.get("/evidence-search")
def evidence_search(q: str | None = None, amount: str | None = None, acc: CompanyAccess = Depends(company_access),
                    db: Session = Depends(get_db)):
    """Candidate supporting documents (invoices, notes, bank lines) to link to a ledger entry."""
    stmt = select(SourceRecord).where(SourceRecord.company_id == acc.company.id, SourceRecord.status != "duplicate")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(SourceRecord.party_name.ilike(like), SourceRecord.doc_number.ilike(like),
                              SourceRecord.description.ilike(like), SourceRecord.reference.ilike(like)))
    rows = db.scalars(stmt.order_by(SourceRecord.id.desc()).limit(50)).all()
    if amount:
        rows = sorted(rows, key=lambda r: 0 if amount in {S.m(r.total_amount), S.m(r.debit_amount), S.m(r.credit_amount)} else 1)
    return [S.record(r, brief=True) for r in rows]


class LinkIn(BaseModel):
    record_id: int


@router.post("/ledger/{entry_id}/evidence", status_code=201)
def link_evidence(entry_id: int, body: LinkIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    l = _get(db, LedgerEntry, entry_id, acc.company.id, "Ledger entry")
    r = _get(db, SourceRecord, body.record_id, acc.company.id, "Record")
    if db.scalar(select(VouchingLink).where(VouchingLink.ledger_entry_id == l.id, VouchingLink.record_id == r.id)):
        raise HTTPException(409, "This document is already linked to the entry.")
    v = _wf(svc.link_evidence, db, acc.company, l, r, acc.user.id)
    db.commit()
    return S.vouching(v)


class LinkReviewIn(BaseModel):
    review_status: str
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/vouching/{link_id}/review")
def review_link(link_id: int, body: LinkReviewIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    v = _get(db, VouchingLink, link_id, acc.company.id, "Vouching link")
    _wf(svc.review_link, db, acc.company, v, acc.user.id, body.review_status, body.comment)
    db.commit()
    return S.vouching(v)


@router.get("/exceptions")
def list_exceptions(status: str | None = None, severity: str | None = None, import_id: int | None = None,
                    acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(AuditException).where(AuditException.company_id == acc.company.id)
    if status == "unresolved":
        stmt = stmt.where(AuditException.status.in_(["open", "needs_information", "under_review"]))
    elif status:
        stmt = stmt.where(AuditException.status == status)
    if severity:
        stmt = stmt.where(AuditException.severity == severity)
    if import_id:
        stmt = stmt.where(AuditException.import_id == import_id)
    xs = db.scalars(stmt).all()
    return sorted([S.exception(x) for x in xs], key=lambda x: (svc.SEVERITY_ORDER.get(x["severity"], 9), -x["id"]))


@router.get("/exceptions/{ex_id}")
def get_exception(ex_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    x = _get(db, AuditException, ex_id, acc.company.id, "Exception")
    cs = db.scalars(select(ExceptionComment).where(ExceptionComment.exception_id == x.id).order_by(ExceptionComment.id)).all()
    out = S.exception(x, cs)
    users = {u.id: u.name for u in db.scalars(select(User))}
    for h in out["history"]:
        h["user_name"] = users.get(h["user_id"], "FinV")
    if x.object_type == "ledger_entry" and x.object_id:
        l = db.get(LedgerEntry, x.object_id)
        out["ledger_entry"] = S.ledger_entry(l) if l else None
    if x.object_type == "tb_line" and x.object_id:
        t = db.get(TrialBalanceLine, x.object_id)
        out["tb_line"] = {"account_code": t.account_code, "account_name": t.account_name, "opening": S.m(t.opening),
                          "debit": S.m(t.debit), "credit": S.m(t.credit), "closing": S.m(t.closing)} if t else None
    return out


class ExceptionUpdate(BaseModel):
    status: str | None = None
    comment: str | None = Field(default=None, max_length=4000)
    assignee_id: int | None = None


@router.post("/exceptions/{ex_id}")
def update_exception(ex_id: int, body: ExceptionUpdate, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    x = _get(db, AuditException, ex_id, acc.company.id, "Exception")
    _wf(svc.update_exception, db, acc.company, x, acc.user.id, body.status, body.comment, body.assignee_id)
    db.commit()
    return get_exception(ex_id, acc, db)


@router.get("/reports/{name}.csv")
def audit_report(name: str, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    cid = acc.company.id
    buf = io.StringIO()
    w = csv.writer(buf)

    def safe(c):
        return ("'" + c) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c

    if name == "exceptions":
        w.writerow(["id", "classification", "status", "severity", "rule_id", "title", "explanation", "suggested_action"])
        for x in db.scalars(select(AuditException).where(AuditException.company_id == cid).order_by(AuditException.id)):
            classification = {"resolved": "resolved", "accepted_with_explanation": "accepted with explanation"}.get(
                x.status, "detected anomaly - unresolved")
            w.writerow([safe(v) for v in [x.id, classification, x.status, x.severity, x.rule_id, x.title, x.explanation,
                                          x.suggested_action or ""]])
    elif name == "vouching":
        w.writerow(["link_id", "ledger_entry_id", "voucher", "account", "amount", "record_id", "outcome", "review_status", "summary", "comment"])
        for v in db.scalars(select(VouchingLink).where(VouchingLink.company_id == cid).order_by(VouchingLink.id)):
            l = db.get(LedgerEntry, v.ledger_entry_id)
            w.writerow([safe(x) for x in [v.id, l.id, l.voucher_no or "", l.account_name or l.account_code or "",
                                          S.m(l.debit or l.credit), v.record_id, v.outcome, v.review_status,
                                          v.result.get("summary", ""), v.comment or ""]])
    elif name == "missing-evidence":
        w.writerow(["exception_id", "status", "ledger_entry_id", "title", "explanation"])
        for x in db.scalars(select(AuditException).where(AuditException.company_id == cid, AuditException.rule_id == "VCH-001")):
            w.writerow([safe(v) for v in [x.id, x.status, x.object_id, x.title, x.explanation]])
    elif name == "review-history":
        users = {u.id: u.name for u in db.scalars(select(User))}
        w.writerow(["time", "user", "action", "object_type", "object_id", "details"])
        for ev in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action.like("audit.%"))
                             .order_by(AuditEvent.id)):
            w.writerow([safe(v) for v in [S.d(ev.created_at), users.get(ev.user_id, ""), ev.action, ev.object_type or "",
                                          ev.object_id or "", str(ev.details or {})]])
    else:
        raise HTTPException(404, "Unknown report.")
    log_event(db, cid, acc.user.id, "audit.report_exported", "audit_report", None, {"report": name})
    db.commit()
    w.writerow([])
    w.writerow(["Note: FinV checks identify anomalies for the auditor's judgement. They are not a statutory audit."])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="audit-{name}.csv"'})


# ---------------------------------------------------------------- report versions and sign-off
class VersionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class SignOffIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    acknowledge_open: bool = False


def _version_out(v: AuditReportVersion, users: dict, full: bool = False) -> dict:
    out = {"id": v.id, "version": v.version, "status": v.status, "note": v.note, "content_hash": v.content_hash,
           "created_by": users.get(v.created_by, ""), "created_at": S.d(v.created_at),
           "signed_by": users.get(v.signed_by, "") if v.signed_by else None, "signed_at": S.d(v.signed_at), "sign_note": v.sign_note,
           "open_exceptions": v.snapshot.get("open_exceptions", 0), "open_high": v.snapshot.get("open_high", 0)}
    if full:
        out["snapshot"] = v.snapshot
    return out


@router.get("/report-versions")
def list_report_versions(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    users = {u.id: u.name for u in db.scalars(select(User))}
    rows = db.scalars(select(AuditReportVersion).where(AuditReportVersion.company_id == acc.company.id)
                      .order_by(AuditReportVersion.version.desc())).all()
    return [_version_out(v, users) for v in rows]


@router.post("/report-versions", status_code=201)
def create_report_version(body: VersionIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    v = report_svc.create_version(db, acc.company, acc.user.id, body.note)
    db.commit()
    return _version_out(v, {u.id: u.name for u in db.scalars(select(User))}, full=True)


def _version(db, acc, version_id: int) -> AuditReportVersion:
    v = db.get(AuditReportVersion, version_id)
    if v is None or v.company_id != acc.company.id:
        raise HTTPException(404, "Report version not found.")
    return v


@router.get("/report-versions/{version_id}")
def get_report_version(version_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _version_out(_version(db, acc, version_id), {u.id: u.name for u in db.scalars(select(User))}, full=True)


@router.post("/report-versions/{version_id}/sign-off")
def sign_off_report_version(version_id: int, body: SignOffIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit")
    v = _version(db, acc, version_id)
    _wf(report_svc.sign_off, db, acc.company, v, acc.user.id, body.note, body.acknowledge_open)
    db.commit()
    return _version_out(v, {u.id: u.name for u in db.scalars(select(User))}, full=True)


# ---------------------------------------------------------------- trial balance from the client's own books
def _fy_bounds(fy: str | None) -> tuple[date, date, str]:
    today = date.today()
    start_year = (today.year if today.month >= 4 else today.year - 1) if not fy else int(fy[:4])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31), f"{start_year}-{str(start_year + 1)[2:]}"


@router.get("/trial-balance/generated")
def generated_trial_balance(financial_year: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
                            acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Trial balance computed from this client's posted FinV entries for one Indian financial year. Nothing is imported,
    invented or adjusted: if the books do not balance, the difference is shown as it is."""
    start, end, fy = _fy_bounds(financial_year)
    cid = acc.company.id
    in_period = (JournalEntry.company_id == cid, JournalEntry.entry_date >= start, JournalEntry.entry_date <= end)
    rows = db.execute(select(Account.id, Account.code, Account.name, Account.type, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
                      .join(JournalLine, JournalLine.account_id == Account.id).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                      .where(*in_period, JournalEntry.status == "posted").group_by(Account.id).order_by(Account.code)).all()
    lines, td, tc = [], Decimal("0"), Decimal("0")
    for aid, code, name, typ, dr, cr in rows:
        dr, cr = dr or Decimal("0"), cr or Decimal("0")
        net = dr - cr
        lines.append({"account_id": aid, "code": code, "name": name, "type": typ, "debit": S.m(dr), "credit": S.m(cr),
                      "closing_debit": S.m(net) if net > 0 else "0.00", "closing_credit": S.m(-net) if net < 0 else "0.00"})
        td, tc = td + dr, tc + cr
    pending = dict(db.execute(select(JournalEntry.status, func.count()).where(*in_period, JournalEntry.status.in_(["approved", "needs_review", "validated"]))
                              .group_by(JournalEntry.status)).all())
    undated = db.scalar(select(func.count()).where(JournalEntry.company_id == cid, JournalEntry.entry_date.is_(None), JournalEntry.status == "posted")) or 0
    diff = td - tc
    limits = ["FinV does not hold opening balances brought forward from earlier years, so every figure is the movement posted in this financial year."]
    if not lines:
        limits.append("No entry has been posted in this financial year, so there is nothing to show yet.")
    if any(pending.values()):
        limits.append(f"{sum(pending.values())} entr{'y is' if sum(pending.values()) == 1 else 'ies are'} not yet posted and left out.")
    if undated:
        limits.append(f"{undated} posted entr{'y has' if undated == 1 else 'ies have'} no date and left out.")
    return {"financial_year": fy, "period_start": str(start), "period_end": str(end), "basis": "Posted FinV entries only",
            "lines": lines, "total_debit": S.m(td), "total_credit": S.m(tc), "difference": S.m(diff), "balanced": diff == 0 and bool(lines),
            "not_included": pending, "limitations": limits}


# ---------------------------------------------------------------- Tally (running instance)
class TallyPullIn(BaseModel):
    url: str | None = Field(default=None, max_length=300)
    tally_company: str | None = Field(default=None, max_length=200)
    date_from: date
    date_to: date


@router.post("/tally/pull")
def tally_pull(body: TallyPullIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Fetch the Day Book from a running TallyPrime for one period and import it. Honest about every failure."""
    acc.require("audit")
    from ..config import settings
    try:
        res = tally_pull_svc.pull_daybook(db, acc.company, acc.user.id, body.url or settings.tally_url,
                                          body.tally_company or acc.company.name, body.date_from, body.date_to)
    except svc.AuditWorkflowError as exc:
        db.commit()                      # keep the failure in the audit trail
        raise HTTPException(exc.status, exc.message)
    except Exception as exc:             # check_tally_url raises the accounting WorkflowError for non-local addresses
        if hasattr(exc, "status") and hasattr(exc, "message"):
            db.commit()
            raise HTTPException(exc.status, exc.message)
        raise
    db.commit()
    return res


@router.get("/tally/status")
def tally_status(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """The last pull attempts for this client: when, what came back, and what failed."""
    evs = db.scalars(select(AuditEvent).where(AuditEvent.company_id == acc.company.id, AuditEvent.action.in_(["tally.pull", "tally.pull_failed"]))
                     .order_by(AuditEvent.id.desc()).limit(10)).all()
    return {"attempts": [{"at": S.d(e.created_at), "ok": e.action == "tally.pull", "details": e.details} for e in evs],
            "last_success": next((S.d(e.created_at) for e in evs if e.action == "tally.pull"), None)}
