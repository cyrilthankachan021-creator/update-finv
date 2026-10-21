from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..engine.rules import rules_catalogue
from ..ingestion.pipeline import IngestError, KINDS
from ..models import (AuditEvent, BankMatch, Document, DuplicateCandidate, JournalEntry, JournalLine, Posting,
                      SourceRecord, User, ValidationResult)
from ..security import CompanyAccess, company_access
from ..services import accounting_service as acc_svc
from ..services import ai_service, documents_service, recon_service, storage
from ..services.audit_log import log_event, verify_chain
from . import serializers as S

router = APIRouter(prefix="/api/companies/{company_id}", tags=["accounting"])


def _get(db: Session, model, obj_id: int, company_id: int, label: str):
    o = db.get(model, obj_id)
    if o is None or o.company_id != company_id:
        raise HTTPException(404, f"{label} not found.")
    return o


# ---------------------------------------------------------------- overview
@router.get("/accounting/overview")
def overview(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    cid = acc.company.id
    counts = dict(db.execute(select(JournalEntry.status, func.count()).where(JournalEntry.company_id == cid)
                             .group_by(JournalEntry.status)).all())
    with_errors = db.scalar(
        select(func.count(func.distinct(JournalEntry.id))).join(
            ValidationResult, (ValidationResult.entry_id == JournalEntry.id) &
            (ValidationResult.entry_version == JournalEntry.version))
        .where(JournalEntry.company_id == cid, JournalEntry.status.in_(["needs_review", "validated"]),
               ValidationResult.status == "error")) or 0
    open_dups = db.scalar(select(func.count()).select_from(DuplicateCandidate)
                          .where(DuplicateCandidate.company_id == cid, DuplicateCandidate.status == "open")) or 0
    docs = db.scalars(select(Document).where(Document.company_id == cid, Document.workspace == "accounting")
                      .order_by(Document.id.desc()).limit(8)).all()
    return {"pending_review": counts.get("needs_review", 0), "validated": counts.get("validated", 0),
            "approved": counts.get("approved", 0), "posted": counts.get("posted", 0),
            "rejected": counts.get("rejected", 0), "issues_found": with_errors, "possible_duplicates": open_dups,
            "recent_documents": [S.document(d) for d in docs],
            "upload": {"max_mb": settings.max_upload_mb, "extensions": [".csv", ".xlsx", ".xml", ".pdf", ".jpg", ".jpeg", ".png"]}}


# ---------------------------------------------------------------- documents
@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...), kind: str = Form("auto"), workspace: str = Form("accounting"),
                          bank_account_code: str | None = Form(None), period_label: str | None = Form(None),
                          period_start: date | None = Form(None), period_end: date | None = Form(None),
                          idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                          acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("audit" if workspace == "audit" else "prepare")
    if kind not in KINDS or workspace not in {"accounting", "audit"}:
        raise HTTPException(422, "Unknown document type.")
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"The file is larger than the {settings.max_upload_mb} MB limit.")
    try:
        doc, created = documents_service.upload(db, acc.company, acc.user.id, file.filename or "upload", data, kind,
                                                workspace, bank_account_code or None, idempotency_key, period_label,
                                                period_start, period_end)
    except IngestError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    db.commit()
    return {**S.document(doc), "idempotent_replay": not created}


@router.get("/documents")
def list_documents(workspace: str | None = None, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    q = select(Document).where(Document.company_id == acc.company.id)
    if workspace:
        q = q.where(Document.workspace == workspace)
    return [S.document(d) for d in db.scalars(q.order_by(Document.id.desc()).limit(200))]


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return S.document(_get(db, Document, doc_id, acc.company.id, "Document"))


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    doc = _get(db, Document, doc_id, acc.company.id, "Document")
    if not doc.storage_path:
        raise HTTPException(404, "The file content is no longer stored.")
    data = storage.load(doc.storage_path)
    log_event(db, acc.company.id, acc.user.id, "document.viewed", "document", doc.id, {"filename": doc.filename})
    db.commit()
    # Only images and PDFs are shown inline; everything else downloads as plain bytes.
    inline = doc.content_type in {"application/pdf", "image/png", "image/jpeg"}
    ctype = doc.content_type if inline else "application/octet-stream"
    disp = "inline" if inline else "attachment"
    return Response(data, media_type=ctype, headers={
        "Content-Disposition": f'{disp}; filename="document-{doc.id}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; object-src 'self'; plugin-types application/pdf",
        "Cache-Control": "no-store"})


@router.post("/documents/{doc_id}/retry")
def retry_document(doc_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    doc = _get(db, Document, doc_id, acc.company.id, "Document")
    documents_service.reprocess(db, acc.company, doc, acc.user.id)
    db.commit()
    return S.document(doc)


class PurgeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/documents/{doc_id}/purge")
def purge_document(doc_id: int, body: PurgeIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    doc = _get(db, Document, doc_id, acc.company.id, "Document")
    res = documents_service.purge_document(db, acc.company, doc, acc.user.id, body.reason)
    db.commit()
    return res


# ---------------------------------------------------------------- records
@router.get("/records")
def list_records(record_type: str | None = None, status: str | None = None, q: str | None = None,
                 date_from: date | None = None, date_to: date | None = None, duplicates_only: bool = False,
                 category: str | None = None, document_id: int | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                 acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(SourceRecord).where(SourceRecord.company_id == acc.company.id)
    if record_type:
        stmt = stmt.where(SourceRecord.record_type == record_type)
    if status:
        stmt = stmt.where(SourceRecord.status == status)
    if document_id:
        stmt = stmt.where(SourceRecord.document_id == document_id)
    if date_from:
        stmt = stmt.where(SourceRecord.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(SourceRecord.doc_date <= date_to)
    if category:
        stmt = stmt.where(SourceRecord.category_hint == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(SourceRecord.party_name.ilike(like), SourceRecord.doc_number.ilike(like),
                              SourceRecord.description.ilike(like), SourceRecord.reference.ilike(like),
                              SourceRecord.party_gstin.ilike(like)))
    if duplicates_only:
        dup_ids = select(DuplicateCandidate.record_id).where(DuplicateCandidate.company_id == acc.company.id,
                                                             DuplicateCandidate.status == "open")
        stmt = stmt.where(SourceRecord.id.in_(dup_ids))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(SourceRecord.doc_date.desc().nullslast(), SourceRecord.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    open_dup = {r for (r,) in db.execute(select(DuplicateCandidate.record_id).where(
        DuplicateCandidate.company_id == acc.company.id, DuplicateCandidate.status == "open")).all()}
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{**S.record(r, brief=True), "has_open_duplicate": r.id in open_dup} for r in rows]}


@router.get("/records/{record_id}")
def get_record(record_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    r = _get(db, SourceRecord, record_id, acc.company.id, "Record")
    entries = db.scalars(select(JournalEntry).where(JournalEntry.source_record_id == r.id).order_by(JournalEntry.id.desc())).all()
    dups = db.scalars(select(DuplicateCandidate).where(or_(DuplicateCandidate.record_id == r.id,
                                                           DuplicateCandidate.matched_record_id == r.id))).all()
    related = []
    if r.original_record_id:
        o = db.get(SourceRecord, r.original_record_id)
        if o:
            related.append({"relation": "original_invoice", **S.record(o, brief=True)})
    for n in db.scalars(select(SourceRecord).where(SourceRecord.original_record_id == r.id)):
        related.append({"relation": "adjusting_note", **S.record(n, brief=True)})
    return {"record": S.record(r), "entries": [S.entry(e, acc_svc.latest_results(db, e)) for e in entries],
            "duplicates": [S.duplicate(c) for c in dups], "related": related}


class RecordPatch(BaseModel):
    fields: dict


_DECIMAL_FIELDS = {"taxable_amount", "cgst", "sgst", "igst", "cess", "total_amount"}


@router.patch("/records/{record_id}")
def patch_record(record_id: int, body: RecordPatch, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    r = _get(db, SourceRecord, record_id, acc.company.id, "Record")
    fields = {}
    for k, v in body.fields.items():
        if v in ("", None):
            fields[k] = None
        elif k in _DECIMAL_FIELDS:
            try:
                fields[k] = Decimal(str(v).replace(",", ""))
            except Exception:
                raise HTTPException(422, f"{k.replace('_', ' ')} must be an amount.")
            if fields[k].as_tuple().exponent < -2:
                raise HTTPException(422, f"{k.replace('_', ' ')} can have at most two decimal places.")
        elif k in {"doc_date", "due_date"}:
            try:
                fields[k] = date.fromisoformat(str(v))
            except ValueError:
                raise HTTPException(422, "Dates must be YYYY-MM-DD.")
        elif k == "note_side":
            if v not in {"sales", "purchase"}:
                raise HTTPException(422, "Note side must be sales or purchase.")
            fields[k] = v
        elif k == "party_gstin":
            fields[k] = str(v).strip().upper()
        else:
            fields[k] = str(v).strip()[:255]
    try:
        acc_svc.update_record(db, acc.company, r, acc.user.id, fields)
    except acc_svc.WorkflowError as exc:
        raise HTTPException(exc.status, exc.message)
    db.commit()
    return get_record(record_id, acc, db)


@router.post("/records/{record_id}/repropose")
def repropose(record_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    r = _get(db, SourceRecord, record_id, acc.company.id, "Record")
    if r.status == "duplicate":
        raise HTTPException(409, "This record is a confirmed duplicate.")
    if r.status == "rejected":
        r.status = "extracted"
    try:
        e = acc_svc.propose_for_record(db, acc.company, r, acc.user.id)
    except acc_svc.WorkflowError as exc:
        raise HTTPException(exc.status, exc.message)
    db.commit()
    return S.entry(e, acc_svc.latest_results(db, e))


@router.post("/records/{record_id}/ai-suggest")
def ai_suggest(record_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    r = _get(db, SourceRecord, record_id, acc.company.id, "Record")
    try:
        e = ai_service.suggest_bank_classification(db, acc.company, r, acc.user.id)
    except acc_svc.WorkflowError as exc:
        db.commit()  # keep the AI request log
        raise HTTPException(exc.status, exc.message)
    db.commit()
    return S.entry(e, acc_svc.latest_results(db, e))


# ---------------------------------------------------------------- entries
@router.get("/entries")
def list_entries(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                 acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(JournalEntry).where(JournalEntry.company_id == acc.company.id)
    if status == "issues":
        err_ids = select(ValidationResult.entry_id).join(
            JournalEntry, (ValidationResult.entry_id == JournalEntry.id) & (ValidationResult.entry_version == JournalEntry.version)
        ).where(ValidationResult.status == "error", ValidationResult.company_id == acc.company.id)
        stmt = stmt.where(JournalEntry.id.in_(err_ids), JournalEntry.status.in_(["needs_review", "validated"]))
    elif status:
        stmt = stmt.where(JournalEntry.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(JournalEntry.entry_date.desc().nullslast(), JournalEntry.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.entry(e, acc_svc.latest_results(db, e)) for e in rows]}


@router.get("/entries/{entry_id}")
def get_entry(entry_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    out = S.entry(e, acc_svc.latest_results(db, e))
    out["source_record"] = S.record(e.source_record) if e.source_record else None
    p = db.scalar(select(Posting).where(Posting.entry_id == e.id))
    out["posting"] = {"id": p.id, "posted_at": S.d(p.posted_at), "posted_by": p.posted_by, "entry_version": p.entry_version} if p else None
    evs = db.scalars(select(AuditEvent).where(AuditEvent.company_id == acc.company.id, AuditEvent.object_type == "journal_entry",
                                              AuditEvent.object_id == e.id).order_by(AuditEvent.id)).all()
    users = {u.id: u.name for u in db.scalars(select(User))}
    out["history"] = [S.event(ev, users) for ev in evs]
    return out


class LineIn(BaseModel):
    account_id: int | None
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = Field(default=None, max_length=255)


class EntryCorrection(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=50)
    entry_date: date | None = None
    narration: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)


def _wf(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except acc_svc.WorkflowError as exc:
        raise HTTPException(exc.status, {"message": exc.message, **exc.details} if exc.details else exc.message)


@router.put("/entries/{entry_id}")
def correct_entry(entry_id: int, body: EntryCorrection, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    _wf(acc_svc.correct_entry, db, acc.company, e, acc.user.id, [l.model_dump() for l in body.lines],
        body.entry_date, body.narration, body.note)
    db.commit()
    return get_entry(entry_id, acc, db)


@router.post("/entries/{entry_id}/revalidate")
def revalidate(entry_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    acc_svc.validate_entry(db, acc.company, e)
    db.commit()
    return get_entry(entry_id, acc, db)


class ApproveIn(BaseModel):
    acknowledge_review: bool = False
    note: str | None = Field(default=None, max_length=1000)


@router.post("/entries/{entry_id}/approve")
def approve(entry_id: int, body: ApproveIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("approve")
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    try:
        acc_svc.approve_entry(db, acc.company, e, acc.user.id, acc.role, body.acknowledge_review, body.note)
    except acc_svc.WorkflowError as exc:
        db.commit()  # keep the refreshed validation results
        raise HTTPException(exc.status, {"message": exc.message, **exc.details} if exc.details else exc.message)
    db.commit()
    return get_entry(entry_id, acc, db)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/entries/{entry_id}/reject")
def reject(entry_id: int, body: RejectIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("approve")
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    _wf(acc_svc.reject_entry, db, acc.company, e, acc.user.id, body.reason)
    db.commit()
    return get_entry(entry_id, acc, db)


@router.post("/entries/{entry_id}/post")
def post(entry_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("post")
    e = _get(db, JournalEntry, entry_id, acc.company.id, "Entry")
    p, created = _wf(acc_svc.post_entry, db, acc.company, e, acc.user.id)
    db.commit()
    return {**get_entry(entry_id, acc, db), "idempotent_replay": not created}


# ---------------------------------------------------------------- duplicates
@router.get("/duplicates")
def list_duplicates(status: str | None = "open", acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(DuplicateCandidate).where(DuplicateCandidate.company_id == acc.company.id)
    if status and status != "all":
        stmt = stmt.where(DuplicateCandidate.status == status)
    return [S.duplicate(c) for c in db.scalars(stmt.order_by(DuplicateCandidate.score.desc(), DuplicateCandidate.id.desc()))]


class DupDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(confirmed_duplicate|not_duplicate)$")
    note: str | None = Field(default=None, max_length=1000)


@router.post("/duplicates/{cand_id}/decision")
def decide_duplicate(cand_id: int, body: DupDecisionIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("approve" if body.decision == "not_duplicate" else "prepare")
    c = _get(db, DuplicateCandidate, cand_id, acc.company.id, "Duplicate candidate")
    _wf(acc_svc.decide_duplicate, db, acc.company, c, acc.user.id, body.decision, body.note)
    db.commit()
    return S.duplicate(c)


# ---------------------------------------------------------------- bank reconciliation
@router.get("/bank/reconciliation")
def reconciliation(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    res = recon_service.run(db, acc.company)
    ids = set(res["unmatched_bank"]) | {m["bank_record_id"] for m in res["matched"]} | {a["bank_record_id"] for a in res["ambiguous"]}
    entry_ids = set(res["unmatched_book"]) | {m["entry_id"] for m in res["matched"]}
    for a in res["ambiguous"]:
        entry_ids |= set(a["candidate_entry_ids"])
    recs = {r.id: S.record(r, brief=True) for r in db.scalars(select(SourceRecord).where(SourceRecord.id.in_(ids)))} if ids else {}
    ents = {}
    for e in (db.scalars(select(JournalEntry).where(JournalEntry.id.in_(entry_ids))) if entry_ids else []):
        ents[e.id] = {"id": e.id, "entry_date": S.d(e.entry_date), "narration": e.narration, "status": e.status,
                      "total_debit": S.m(sum((l.debit for l in e.lines), Decimal("0")))}
    matches = db.scalars(select(BankMatch).where(BankMatch.company_id == acc.company.id, BankMatch.status == "confirmed")).all()
    res["confirmed_matches"] = [{"id": m.id, "bank_record_id": m.bank_record_id, "entry_id": m.entry_id, "method": m.method}
                                for m in matches]
    res["records"], res["entries"] = recs, ents
    res["summary"] = {"matched": len(res["matched"]), "unmatched_bank": len(res["unmatched_bank"]),
                      "unmatched_book": len(res["unmatched_book"]), "ambiguous": len(res["ambiguous"])}
    return res


class MatchIn(BaseModel):
    bank_record_id: int
    entry_id: int


@router.post("/bank/matches", status_code=201)
def manual_match(body: MatchIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    m = _wf(recon_service.manual_match, db, acc.company, body.bank_record_id, body.entry_id, acc.user.id)
    db.commit()
    return {"id": m.id}


@router.post("/bank/confirm-auto")
def confirm_auto(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    n = recon_service.confirm_auto(db, acc.company, acc.user.id)
    db.commit()
    return {"confirmed": n}


@router.delete("/bank/matches/{match_id}")
def remove_match(match_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    _wf(recon_service.remove_match, db, acc.company, match_id, acc.user.id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- reports & exports
def _csv(rows: list[list], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        # neutralise spreadsheet formula injection in exported text
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(c) else c
                    for c in r])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _is_number(s: str) -> bool:
    try:
        Decimal(s)
        return True
    except Exception:
        return False


def _period(stmt, col, date_from, date_to):
    if date_from:
        stmt = stmt.where(col >= date_from)
    if date_to:
        stmt = stmt.where(col <= date_to)
    return stmt


@router.get("/reports/summary")
def report_summary(date_from: date | None = None, date_to: date | None = None,
                   acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    cid = acc.company.id
    stmt = _period(select(JournalEntry).where(JournalEntry.company_id == cid), JournalEntry.entry_date, date_from, date_to)
    entries = db.scalars(stmt).all()
    by_status: dict[str, dict] = {}
    for e in entries:
        s = by_status.setdefault(e.status, {"count": 0, "debit": Decimal("0"), "credit": Decimal("0")})
        s["count"] += 1
        s["debit"] += sum((l.debit for l in e.lines), Decimal("0"))
        s["credit"] += sum((l.credit for l in e.lines), Decimal("0"))
    rule_counts = dict(db.execute(
        select(ValidationResult.rule_id, func.count()).join(
            JournalEntry, (ValidationResult.entry_id == JournalEntry.id) & (ValidationResult.entry_version == JournalEntry.version))
        .where(ValidationResult.company_id == cid, ValidationResult.status == "error",
               JournalEntry.status.in_(["needs_review", "validated"])).group_by(ValidationResult.rule_id)).all())
    failed_docs = db.scalar(select(func.count()).select_from(Document).where(Document.company_id == cid, Document.status == "failed"))
    recon = recon_service.run(db, acc.company)
    return {"entries_by_status": {k: {"count": v["count"], "debit": S.m(v["debit"]), "credit": S.m(v["credit"])}
                                  for k, v in by_status.items()},
            "open_errors_by_rule": rule_counts, "processing_failures": failed_docs,
            "possible_duplicates": db.scalar(select(func.count()).select_from(DuplicateCandidate).where(
                DuplicateCandidate.company_id == cid, DuplicateCandidate.status == "open")),
            "reconciliation": {"matched": len(recon["matched"]), "unmatched_bank": len(recon["unmatched_bank"]),
                               "unmatched_book": len(recon["unmatched_book"]), "ambiguous": len(recon["ambiguous"])},
            "note": "Imported data, proposals, validated, approved and posted entries are counted separately by status."}


@router.get("/reports/{name}.csv")
def report_csv(name: str, date_from: date | None = None, date_to: date | None = None,
               acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    cid = acc.company.id
    if name in {"journal", "posted-journal", "pending-review", "validation-errors"}:
        stmt = _period(select(JournalEntry).where(JournalEntry.company_id == cid), JournalEntry.entry_date, date_from, date_to)
        if name == "posted-journal":
            stmt = stmt.where(JournalEntry.status == "posted")
        elif name == "pending-review":
            stmt = stmt.where(JournalEntry.status == "needs_review")
        entries = db.scalars(stmt.order_by(JournalEntry.entry_date, JournalEntry.id)).all()
        if name == "validation-errors":
            rows = [["entry_id", "entry_date", "status", "rule_id", "rule_version", "result", "message"]]
            for e in entries:
                for r in acc_svc.latest_results(db, e):
                    if r.status in {"error", "needs_review", "warning"}:
                        rows.append([e.id, S.d(e.entry_date), e.status, r.rule_id, r.rule_version, r.status, r.message])
            return _csv(rows, f"{name}.csv")
        rows = [["entry_id", "entry_date", "status", "version", "origin", "narration", "account_code", "account_name",
                 "debit", "credit", "line_description", "source_record_id", "source_document_id"]]
        for e in entries:
            for l in e.lines:
                rows.append([e.id, S.d(e.entry_date), e.status, e.version, e.origin, e.narration,
                             l.account.code if l.account else "", l.account.name if l.account else "",
                             S.m(l.debit), S.m(l.credit), l.description or "", e.source_record_id or "",
                             e.source_record.document_id if e.source_record else ""])
        return _csv(rows, f"{name}.csv")
    if name == "duplicates":
        rows = [["candidate_id", "status", "strength", "kind", "record_id", "matched_record_id", "explanation", "decision_note"]]
        for c in db.scalars(select(DuplicateCandidate).where(DuplicateCandidate.company_id == cid)):
            rows.append([c.id, c.status, c.strength, c.kind, c.record_id, c.matched_record_id, c.explanation, c.decision_note or ""])
        return _csv(rows, "duplicates.csv")
    if name == "reconciliation":
        res = recon_service.run(db, acc.company)
        rows = [["result", "bank_record_id", "entry_id", "detail"]]
        rows += [["matched", m["bank_record_id"], m["entry_id"], m["method"]] for m in res["matched"]]
        rows += [["ambiguous", a["bank_record_id"], " ".join(map(str, a["candidate_entry_ids"])), a["reason"]] for a in res["ambiguous"]]
        rows += [["unmatched_bank", b, "", ""] for b in res["unmatched_bank"]]
        rows += [["unmatched_book", "", e, ""] for e in res["unmatched_book"]]
        rows += [["near_miss", n["bank_record_id"], n["entry_id"], f"difference {n['difference']}"] for n in res["near_misses"]]
        return _csv(rows, "reconciliation.csv")
    if name == "activity":
        users = {u.id: u.name for u in db.scalars(select(User))}
        rows = [["event_id", "time", "user", "action", "object_type", "object_id", "details", "hash"]]
        for ev in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid).order_by(AuditEvent.id)):
            rows.append([ev.id, S.d(ev.created_at), users.get(ev.user_id, ""), ev.action, ev.object_type or "",
                         ev.object_id or "", str(ev.details or {}), ev.hash])
        return _csv(rows, "activity.csv")
    raise HTTPException(404, "Unknown report.")


# ---------------------------------------------------------------- activity / audit trail
@router.get("/activity")
def activity(limit: int = Query(100, ge=1, le=500), object_type: str | None = None, object_id: int | None = None,
             acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    stmt = select(AuditEvent).where(AuditEvent.company_id == acc.company.id)
    if object_type:
        stmt = stmt.where(AuditEvent.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditEvent.object_id == object_id)
    users = {u.id: u.name for u in db.scalars(select(User))}
    return [S.event(ev, users) for ev in db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit))]


@router.get("/activity/verify")
def activity_verify(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return verify_chain(db, acc.company.id)


@router.get("/rules")
def rules(acc: CompanyAccess = Depends(company_access)):
    return rules_catalogue()


@router.get("/ledger")
def general_ledger(account_id: int | None = None, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Posted lines only: FinV's internal general ledger."""
    stmt = (select(JournalLine, JournalEntry).join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(JournalEntry.company_id == acc.company.id, JournalEntry.status == "posted"))
    if account_id:
        stmt = stmt.where(JournalLine.account_id == account_id)
    out = []
    for l, e in db.execute(stmt.order_by(JournalEntry.entry_date, JournalEntry.id)).all():
        out.append({"entry_id": e.id, "entry_date": S.d(e.entry_date), "narration": e.narration,
                    "account_code": l.account.code if l.account else None, "account_name": l.account.name if l.account else None,
                    "debit": S.m(l.debit), "credit": S.m(l.credit)})
    return out


# ---------------------------------------------------------------- Tally
from ..services import tally_service  # noqa: E402


@router.get("/tally/status")
def tally_status(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    from ..models import TallyExport
    pending = tally_service.pending_entries(db, acc.company)
    exported = db.scalars(select(TallyExport).where(TallyExport.company_id == acc.company.id)
                          .order_by(TallyExport.id.desc())).all()
    return {"pending": [{"id": e.id, "entry_date": S.d(e.entry_date), "narration": e.narration,
                         "voucher_type": tally_service._voucher_type(e),
                         "amount": S.m(sum((l.debit for l in e.lines), Decimal("0")))} for e in pending],
            "exported_count": len(exported),
            "recent_exports": [{"entry_id": x.entry_id, "batch_id": x.batch_id, "method": x.method,
                                "at": S.d(x.created_at)} for x in exported[:20]],
            "default_url": settings.tally_url, "default_company_name": acc.company.name}


def _xml(acc, db, include_exported: bool, tally_company: str | None, which: str):
    entries = tally_service.pending_entries(db, acc.company, include_exported)
    if not entries:
        raise HTTPException(404, "There are no posted entries waiting to be exported.")
    masters, vouchers, _ = _wf(tally_service.build_xml, acc.company, entries, tally_company or acc.company.name)
    data = masters if which == "masters" else vouchers
    return Response(data, media_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="finv-tally-{which}.xml"'})


@router.get("/tally/masters.xml")
def tally_masters(include_exported: bool = False, tally_company: str | None = None,
                  acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("post")
    return _xml(acc, db, include_exported, tally_company, "masters")


@router.get("/tally/vouchers.xml")
def tally_vouchers(include_exported: bool = False, tally_company: str | None = None,
                   acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("post")
    return _xml(acc, db, include_exported, tally_company, "vouchers")


class MarkIn(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/tally/mark-exported")
def tally_mark(body: MarkIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("post")
    batch = _wf(tally_service.mark_exported, db, acc.company, acc.user.id, body.entry_ids)
    db.commit()
    return {"batch": batch, "marked": len(body.entry_ids)}


class PushIn(BaseModel):
    url: str | None = Field(default=None, max_length=200)
    tally_company: str | None = Field(default=None, max_length=200)
    include_exported: bool = False


@router.post("/tally/push")
def tally_push(body: PushIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("post")
    try:
        res = tally_service.push(db, acc.company, acc.user.id, body.url or settings.tally_url,
                                 body.tally_company or acc.company.name, body.include_exported)
    except acc_svc.WorkflowError as exc:
        db.commit()  # keep the failure in the audit trail
        raise HTTPException(exc.status, {"message": exc.message, **exc.details} if exc.details else exc.message)
    db.commit()
    return res
