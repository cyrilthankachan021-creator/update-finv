"""Upload -> store (encrypted) -> extract -> records -> duplicates -> proposed entries -> validation."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..engine.bank_validation import bank_report
from ..engine.normalize import sha256_bytes
from ..ingestion.pipeline import IngestError, check_file, extract
from ..models import Company, CompanySettings, Document, JournalEntry, LedgerEntry, SourceRecord, utcnow
from . import accounting_service as acc
from . import audit_service
from . import storage
from .audit_log import log_event

CONTENT_TYPES = {".csv": "text/csv", ".xml": "application/xml", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def upload(db: Session, company: Company, user_id: int, filename: str, data: bytes, kind: str,
           workspace: str, bank_account_code: str | None = None, idempotency_key: str | None = None,
           period_label: str | None = None, period_start=None, period_end=None) -> tuple[Document, bool]:
    """Returns (document, created). Repeat calls with the same idempotency key return the first document."""
    if idempotency_key:
        prior = db.scalar(select(Document).where(Document.company_id == company.id,
                                                 Document.idempotency_key == idempotency_key))
        if prior:
            return prior, False
    safe_name = filename.replace("\\", "/").split("/")[-1][:200] or "upload"
    ext = check_file(data, safe_name, settings.max_upload_mb * 1024 * 1024)
    digest = sha256_bytes(data)
    doc = Document(company_id=company.id, filename=safe_name, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
                   size_bytes=len(data), sha256=digest, kind=kind, workspace=workspace, uploaded_by=user_id,
                   idempotency_key=idempotency_key, status="processing")
    same = db.scalar(select(Document).where(Document.company_id == company.id, Document.sha256 == digest,
                                            Document.status.in_(["processed", "uploaded", "processing"]))
                     .order_by(Document.id))
    db.add(doc)
    db.flush()
    if same:
        # Identical file bytes: do not extract again (that would duplicate every record).
        doc.status = "repeated_upload"
        doc.duplicate_of_id = same.id
        doc.extraction_notes = [f"This file is identical (SHA-256) to '{same.filename}' uploaded earlier "
                                f"(document #{same.id}). It was not processed again, so no records were duplicated."]
        log_event(db, company.id, user_id, "document.repeated_upload", "document", doc.id,
                  {"duplicate_of": same.id, "sha256": digest})
        return doc, True
    doc.storage_path = storage.save(company.id, data)
    log_event(db, company.id, user_id, "document.uploaded", "document", doc.id,
              {"filename": safe_name, "kind": kind, "sha256": digest, "size": len(data)})
    try:
        ex = extract(data, safe_name, kind, company.gstin)
    except IngestError as exc:
        doc.status = "failed"
        doc.error = str(exc)
        log_event(db, company.id, user_id, "document.failed", "document", doc.id, {"error": str(exc)})
        return doc, True
    notes = list(ex.notes)
    notes += [f"Row {e['row']}: {e['message']}" for e in ex.row_errors[:50]]
    if ex.kind in {"trial_balance", "ledger"}:
        doc.kind = ex.kind
        skipped = _drop_already_imported(db, company, ex)
        if skipped:
            notes.append(f"{skipped} ledger line(s) were already imported earlier (same voucher, date, account and amount) and were not added again.")
        if ex.kind == "ledger" and not ex.ledger_lines:
            doc.record_count = 0
            notes.append("Nothing new to import from this file.")
        else:
            imp = audit_service.store_import(db, company, doc, ex, period_label, period_start, period_end)
            doc.record_count = len(ex.tb_lines) + len(ex.ledger_lines)
            notes.append(f"Imported as {ex.kind.replace('_', ' ')} #{imp.id}.")
    else:
        doc.kind = kind if kind != "auto" else ex.kind
        recs = acc.store_records(db, company, doc, ex.records, bank_account_code)
        notes += acc.bank_balance_notes(recs)
        if doc.kind == "bank_statement":
            doc.validation = bank_report(ex.records, ex.row_errors)
        for r in recs:
            acc.detect_duplicates(db, r)
        for r in recs:
            acc.propose_for_record(db, company, r, user_id)
        doc.record_count = len(recs)
    doc.status = "processed"
    doc.extraction_notes = notes
    log_event(db, company.id, user_id, "document.processed", "document", doc.id,
              {"kind": doc.kind, "records": doc.record_count, "row_errors": len(ex.row_errors)})
    return doc, True


def _drop_already_imported(db: Session, company: Company, ex) -> int:
    """Ledger lines that carry a voucher number and match one already stored for this client are left out, so importing an
    overlapping export (or syncing Tally twice) cannot double the books. Lines without a voucher number are always kept."""
    if ex.kind != "ledger" or not ex.ledger_lines:
        return 0
    have = set(db.execute(select(LedgerEntry.voucher_no, LedgerEntry.voucher_type, LedgerEntry.entry_date, LedgerEntry.account_name,
                                 LedgerEntry.debit, LedgerEntry.credit).where(LedgerEntry.company_id == company.id)).all())
    keep, skipped, seen = [], 0, set()
    for l in ex.ledger_lines:
        key = (l["voucher_no"], l["voucher_type"], l["entry_date"], l["account_name"], l["debit"], l["credit"])
        if l["voucher_no"] and key in have:
            skipped += 1
            continue
        keep.append(l)
    ex.ledger_lines = keep
    return skipped


def reprocess(db: Session, company: Company, doc: Document, user_id: int) -> Document:
    """Retry a failed document from its stored (encrypted) bytes."""
    if doc.status != "failed" or not doc.storage_path:
        raise acc.WorkflowError("Only failed documents with stored content can be retried.")
    data = storage.load(doc.storage_path)
    try:
        ex = extract(data, doc.filename, doc.kind, company.gstin)
    except IngestError as exc:
        doc.error = str(exc)
        return doc
    doc.error = None
    if ex.kind in {"trial_balance", "ledger"}:
        audit_service.store_import(db, company, doc, ex, None, None, None)
        doc.record_count = len(ex.tb_lines) + len(ex.ledger_lines)
    else:
        recs = acc.store_records(db, company, doc, ex.records, None)
        if doc.kind == "bank_statement":
            doc.validation = bank_report(ex.records, ex.row_errors)
        for r in recs:
            acc.detect_duplicates(db, r)
        for r in recs:
            acc.propose_for_record(db, company, r, user_id)
        doc.record_count = len(recs)
    doc.status = "processed"
    doc.extraction_notes = list(ex.notes)
    log_event(db, company.id, user_id, "document.reprocessed", "document", doc.id, {"records": doc.record_count})
    return doc


def purge_document(db: Session, company: Company, doc: Document, user_id: int, reason: str) -> dict:
    """Delete the stored file. Extracted records are deleted unless an entry was approved/posted from them,
    in which case the records stay (they are accounting evidence) and only the file content is removed."""
    locked = db.scalar(select(JournalEntry.id).join(SourceRecord, JournalEntry.source_record_id == SourceRecord.id)
                       .where(SourceRecord.document_id == doc.id, JournalEntry.status.in_(["approved", "posted"])).limit(1))
    removed_file = storage.secure_delete(doc.storage_path)
    deleted_records = 0
    if not locked:
        for r in db.scalars(select(SourceRecord).where(SourceRecord.document_id == doc.id)).all():
            for e in db.scalars(select(JournalEntry).where(JournalEntry.source_record_id == r.id)).all():
                db.delete(e)
            db.delete(r)
            deleted_records += 1
    doc.storage_path = None
    doc.status = "purged"
    doc.purged_at = utcnow()
    log_event(db, company.id, user_id, "document.purged", "document", doc.id,
              {"reason": reason, "file_removed": removed_file, "records_deleted": deleted_records,
               "records_kept_because_posted": bool(locked)})
    db.flush()
    return {"file_removed": removed_file, "records_deleted": deleted_records, "records_kept": bool(locked)}


def apply_retention(db: Session, company: Company, user_id: int | None) -> dict:
    s = db.get(CompanySettings, company.id)
    if not s or not s.retention_days:
        return {"purged": 0, "message": "No retention period is set."}
    cutoff = utcnow() - timedelta(days=s.retention_days)
    docs = db.scalars(select(Document).where(Document.company_id == company.id, Document.created_at < cutoff,
                                             Document.status != "purged")).all()
    for d in docs:
        purge_document(db, company, d, user_id, f"Retention period of {s.retention_days} days")
    return {"purged": len(docs), "cutoff": cutoff.isoformat()}
