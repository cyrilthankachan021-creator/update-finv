"""Glue between the database and the deterministic engine for the Accounting workspace."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engine import accounting as eng
from ..engine.duplicates import DupRecord, bank_running_balance, find_matches, line_item_check
from ..engine.money import ZERO, q
from ..engine.normalize import fingerprint, norm_doc_number
from ..engine.rules import Status
from ..models import (Account, AccountingPeriod, Company, DuplicateCandidate, JournalEntry, JournalLine, Policy,
                      Posting, SourceRecord, ValidationResult, Document, utcnow)
from .audit_log import log_event

ACTIVE_ENTRY = {"approved", "posted"}
OPEN_ENTRY = {"needs_review", "validated"}
INVOICE_TYPES = {"purchase_invoice", "sales_invoice"}


class WorkflowError(Exception):
    """A business-rule refusal with a plain-language message (mapped to HTTP 409/403 by the API)."""
    def __init__(self, message: str, status: int = 409, details: dict | None = None):
        super().__init__(message)
        self.message, self.status, self.details = message, status, details or {}


# ------------------------------------------------------------------ conversions
def chart_for(db: Session, company_id: int) -> eng.Chart:
    accts = db.scalars(select(Account).where(Account.company_id == company_id)).all()
    return eng.Chart([eng.AccountRef(a.id, a.code, a.name, a.type, a.role, a.is_active) for a in accts])


def periods_for(db: Session, company_id: int) -> list[eng.Period]:
    ps = db.scalars(select(AccountingPeriod).where(AccountingPeriod.company_id == company_id)).all()
    return [eng.Period(p.name, p.start_date, p.end_date, p.locked) for p in ps]


def approved_policies(db: Session, company_id: int, on: date | None = None) -> list[dict]:
    ps = db.scalars(select(Policy).where(Policy.company_id == company_id, Policy.status == "approved")).all()
    return [{"id": p.id, "key": p.key, "version": p.version, "kind": p.kind, "definition": p.definition}
            for p in ps if not (on and p.effective_from and p.effective_from > on)]


def to_record_data(r: SourceRecord) -> eng.RecordData:
    return eng.RecordData(
        id=r.id, record_type=r.record_type, note_side=r.note_side, party_name=r.party_name, party_gstin=r.party_gstin,
        doc_number=r.doc_number, doc_date=r.doc_date, original_doc_number=r.original_doc_number,
        place_of_supply=r.place_of_supply, taxable=r.taxable_amount, cgst=r.cgst, sgst=r.sgst, igst=r.igst, cess=r.cess,
        total=r.total_amount, category_hint=r.category_hint, bank_account_code=r.bank_account_code,
        description=r.description, reference=r.reference, debit=r.debit_amount, credit=r.credit_amount)


def to_dup_record(r: SourceRecord) -> DupRecord:
    return DupRecord(id=r.id, record_type=r.record_type, party_name=r.party_name, party_gstin=r.party_gstin,
                     doc_number=r.doc_number, doc_date=r.doc_date, taxable=r.taxable_amount, cgst=r.cgst, sgst=r.sgst,
                     igst=r.igst, cess=r.cess, total=r.total_amount, description=r.description, reference=r.reference,
                     debit=r.debit_amount, credit=r.credit_amount, document_id=r.document_id,
                     file_sha256=r.document.sha256 if r.document else None, line_items=r.line_items)


# ------------------------------------------------------------------ context building
def find_original(db: Session, rec: SourceRecord) -> SourceRecord | None:
    if rec.record_type not in {"credit_note", "debit_note"} or not rec.original_doc_number:
        return None
    norm = norm_doc_number(rec.original_doc_number)
    types = INVOICE_TYPES
    if rec.note_side == "sales":
        types = {"sales_invoice"}
    elif rec.note_side == "purchase":
        types = {"purchase_invoice"}
    cands = db.scalars(select(SourceRecord).where(
        SourceRecord.company_id == rec.company_id, SourceRecord.doc_number_norm == norm,
        SourceRecord.record_type.in_(types), SourceRecord.status.notin_(["duplicate", "rejected"]))
        .order_by(SourceRecord.id)).all()
    if not cands:
        return None
    from ..engine.normalize import norm_party
    same_party = [c for c in cands if (rec.party_gstin and c.party_gstin == rec.party_gstin)
                  or (norm_party(rec.party_name) and norm_party(c.party_name) == norm_party(rec.party_name))]
    if same_party:
        return same_party[0]
    return cands[0] if len(cands) == 1 else None


def prior_notes_total(db: Session, rec: SourceRecord, original: SourceRecord) -> Decimal:
    others = db.scalars(select(SourceRecord).where(
        SourceRecord.company_id == rec.company_id, SourceRecord.original_record_id == original.id,
        SourceRecord.record_type == rec.record_type, SourceRecord.id != rec.id,
        SourceRecord.status.notin_(["duplicate", "rejected"]))).all()
    return sum((o.total_amount or ZERO for o in others), ZERO)


def open_items(db: Session, company_id: int) -> list[eng.OpenItem]:
    invoices = db.scalars(select(SourceRecord).where(
        SourceRecord.company_id == company_id, SourceRecord.record_type.in_(INVOICE_TYPES),
        SourceRecord.status.notin_(["duplicate", "rejected"]))).all()
    settled: dict[int, Decimal] = {}
    entries = db.scalars(select(JournalEntry).where(JournalEntry.company_id == company_id,
                                                    JournalEntry.status.in_(ACTIVE_ENTRY))).all()
    for e in entries:
        mid = (e.explanation or {}).get("matched_record_id")
        if mid and e.source_record and e.source_record.record_type == "bank_txn":
            amt = e.source_record.debit_amount or e.source_record.credit_amount or ZERO
            settled[mid] = settled.get(mid, ZERO) + amt
    out = []
    for inv in invoices:
        if inv.total_amount is None:
            continue
        outstanding = inv.total_amount - settled.get(inv.id, ZERO)
        if outstanding > 0:
            out.append(eng.OpenItem(to_record_data(inv), outstanding))
    return out


def open_duplicate_summaries(db: Session, record_id: int) -> list[dict]:
    cs = db.scalars(select(DuplicateCandidate).where(DuplicateCandidate.record_id == record_id,
                                                     DuplicateCandidate.status == "open")).all()
    return [{"candidate_id": c.id, "matched_record_id": c.matched_record_id, "strength": c.strength, "kind": c.kind}
            for c in cs]


def build_context(db: Session, company: Company, rec: SourceRecord | None, exclude_entry_id: int | None = None,
                  ai_suggestion: dict | None = None) -> eng.Context:
    ctx = eng.Context(chart=chart_for(db, company.id), periods=periods_for(db, company.id),
                      company_state=company.state_code or (company.gstin[:2] if company.gstin else None),
                      policies=approved_policies(db, company.id, rec.doc_date if rec else None),
                      ai_suggestion=ai_suggestion)
    if rec is None:
        return ctx
    if rec.record_type in {"credit_note", "debit_note"}:
        orig = db.get(SourceRecord, rec.original_record_id) if rec.original_record_id else find_original(db, rec)
        if orig is not None:
            ctx.original = to_record_data(orig)
            ctx.prior_notes_total = prior_notes_total(db, rec, orig)
    if rec.record_type == "bank_txn":
        ctx.open_items = open_items(db, company.id)
    q_ = select(func.count()).select_from(JournalEntry).where(
        JournalEntry.source_record_id == rec.id, JournalEntry.status.in_(ACTIVE_ENTRY))
    if exclude_entry_id:
        q_ = q_.where(JournalEntry.id != exclude_entry_id)
    ctx.other_active_entries = db.scalar(q_) or 0
    ctx.open_duplicates = open_duplicate_summaries(db, rec.id)
    return ctx


# ------------------------------------------------------------------ duplicates
def detect_duplicates(db: Session, rec: SourceRecord) -> list[DuplicateCandidate]:
    # Records already flagged as exact copies of an earlier record are not compared again: the new record
    # is compared with the canonical (earliest) copy only, so one problem is reported once.
    known_copies = select(DuplicateCandidate.record_id).where(
        DuplicateCandidate.company_id == rec.company_id, DuplicateCandidate.kind == "duplicate",
        DuplicateCandidate.strength == "exact", DuplicateCandidate.status != "not_duplicate")
    existing = db.scalars(select(SourceRecord).where(
        SourceRecord.company_id == rec.company_id, SourceRecord.id != rec.id,
        SourceRecord.id < rec.id,  # compare against earlier records only; the newer record is flagged
        SourceRecord.status.notin_(["rejected", "duplicate"]),
        SourceRecord.id.notin_(known_copies),
        SourceRecord.record_type == rec.record_type)).all()
    matches = find_matches(to_dup_record(rec), [to_dup_record(e) for e in existing])
    created = []
    for m in matches:
        exists = db.scalar(select(DuplicateCandidate).where(DuplicateCandidate.record_id == rec.id,
                                                            DuplicateCandidate.matched_record_id == m.matched_id))
        if exists:
            continue
        c = DuplicateCandidate(company_id=rec.company_id, record_id=rec.id, matched_record_id=m.matched_id, kind=m.kind,
                               strength=m.strength, score=m.score, signals={**m.signals, "recommended_action": m.action},
                               explanation=m.explanation)
        # "possible" matches (e.g. recurring rent) are informational: they don't block posting by default
        if m.strength == "possible":
            c.status = "open"
        db.add(c)
        created.append(c)
    li = line_item_check(to_dup_record(rec))
    if li and not li["ok"]:
        rec.provenance = {**(rec.provenance or {}), "_line_item_check": li}
    db.flush()
    return created


# ------------------------------------------------------------------ entries
def _apply_proposal(db: Session, entry: JournalEntry, proposal: eng.Proposal) -> None:
    entry.lines.clear()
    for l in proposal.lines:
        entry.lines.append(JournalLine(account_id=l.account_id, debit=l.debit, credit=l.credit, description=l.description))
    entry.treatment = proposal.treatment
    entry.origin = proposal.origin
    entry.explanation = {**proposal.explanation, "question": proposal.question}


def propose_for_record(db: Session, company: Company, rec: SourceRecord, user_id: int | None,
                       ai_suggestion: dict | None = None) -> JournalEntry:
    """Create (or refresh) the proposed entry for a record. Approved/posted entries are never changed."""
    if rec.record_type in {"credit_note", "debit_note"} and rec.original_record_id is None:
        orig = find_original(db, rec)
        if orig:
            rec.original_record_id = orig.id
    ctx = build_context(db, company, rec, ai_suggestion=ai_suggestion)
    proposal = eng.propose(to_record_data(rec), ctx)
    entry = db.scalar(select(JournalEntry).where(JournalEntry.source_record_id == rec.id,
                                                 JournalEntry.status.in_(OPEN_ENTRY)).order_by(JournalEntry.id.desc()))
    if entry is None:
        if db.scalar(select(func.count()).select_from(JournalEntry).where(
                JournalEntry.source_record_id == rec.id, JournalEntry.status.in_(ACTIVE_ENTRY))):
            raise WorkflowError("This record already has an approved or posted entry.")
        entry = JournalEntry(company_id=company.id, source_record_id=rec.id, created_by=user_id)
        db.add(entry)
    else:
        entry.version += 1
    entry.entry_date = rec.doc_date
    entry.narration = _narration(rec)
    _apply_proposal(db, entry, proposal)
    db.flush()
    validate_entry(db, company, entry)
    return entry


def _narration(rec: SourceRecord) -> str:
    if rec.record_type == "bank_txn":
        return (rec.description or "Bank transaction")[:250]
    kind = rec.record_type.replace("_", " ").title()
    return f"{kind} {rec.doc_number or ''} — {rec.party_name or 'party not identified'}".strip()


def validate_entry(db: Session, company: Company, entry: JournalEntry) -> list:
    rec = entry.source_record if entry.source_record_id else None
    if rec is None and entry.source_record_id:
        rec = db.get(SourceRecord, entry.source_record_id)
    ctx = build_context(db, company, rec, exclude_entry_id=entry.id)
    # raw precision is kept so the precision rule can see it
    lines = [eng.LineData(l.account_id, Decimal(l.debit), Decimal(l.credit), l.description) for l in entry.lines]
    question = (entry.explanation or {}).get("question")
    results = eng.validate(lines, entry.entry_date, to_record_data(rec) if rec else None, ctx, question)
    db.query(ValidationResult).filter(ValidationResult.entry_id == entry.id,
                                      ValidationResult.entry_version == entry.version).delete()
    for r in results:
        db.add(ValidationResult(company_id=company.id, entry_id=entry.id, record_id=rec.id if rec else None,
                                entry_version=entry.version, rule_id=r.rule_id, rule_version=r.rule_version,
                                status=r.status.value, message=r.message, evidence=r.evidence))
    eligible = eng.is_posting_eligible(results)
    entry.posting_eligible = eligible
    if entry.status in OPEN_ENTRY:
        entry.status = "validated" if eligible else "needs_review"
        if rec and rec.status not in {"duplicate", "rejected", "posted", "approved"}:
            rec.status = entry.status if entry.lines else "needs_review"
            rec.review_question = question
    db.flush()
    return results


def latest_results(db: Session, entry: JournalEntry) -> list[ValidationResult]:
    return db.scalars(select(ValidationResult).where(ValidationResult.entry_id == entry.id,
                                                     ValidationResult.entry_version == entry.version)
                      .order_by(ValidationResult.id)).all()


def correct_entry(db: Session, company: Company, entry: JournalEntry, user_id: int, lines: list[dict],
                  entry_date: date | None, narration: str | None, note: str | None) -> JournalEntry:
    if entry.status not in OPEN_ENTRY:
        raise WorkflowError(f"A {entry.status} entry cannot be edited. Rejected entries can be re-proposed from the record.")
    chart = chart_for(db, company.id)
    before = [{"account_id": l.account_id, "debit": str(l.debit), "credit": str(l.credit)} for l in entry.lines]
    entry.lines.clear()
    for l in lines:
        acct = chart.by_id(l.get("account_id"))
        if l.get("account_id") is not None and acct is None:
            raise WorkflowError("A line uses a ledger that does not belong to this company.", 422)
        entry.lines.append(JournalLine(account_id=l.get("account_id"), debit=Decimal(str(l.get("debit") or "0")),
                                       credit=Decimal(str(l.get("credit") or "0")), description=l.get("description")))
    if entry_date:
        entry.entry_date = entry_date
    if narration is not None:
        entry.narration = narration
    entry.version += 1
    entry.origin = "manual"
    entry.last_edited_by = user_id
    expl = dict(entry.explanation or {})
    if expl.get("question"):
        expl["resolved_question"] = expl["question"]
    expl["question"] = None
    expl["corrected_by_user"] = True
    if note:
        expl["correction_note"] = note
    entry.explanation = expl
    db.flush()
    validate_entry(db, company, entry)
    log_event(db, company.id, user_id, "entry.corrected", "journal_entry", entry.id,
              {"version": entry.version, "before": before,
               "after": [{"account_id": l.account_id, "debit": str(l.debit), "credit": str(l.credit)} for l in entry.lines],
               "note": note, "result": entry.status})
    _maybe_suggest_policy(db, company, entry, user_id)
    return entry


def _maybe_suggest_policy(db: Session, company: Company, entry: JournalEntry, user_id: int) -> None:
    """A correction may become a *proposed* company policy. It never takes effect until approved."""
    rec = entry.source_record
    if not rec or rec.record_type not in {"purchase_invoice"} or not (rec.party_gstin or rec.party_name):
        return
    chart = chart_for(db, company.id)
    debit_main = [chart.by_id(l.account_id) for l in entry.lines if l.debit > 0]
    debit_main = [a for a in debit_main if a and not (a.role or "").startswith("input_")]
    if len(debit_main) != 1:
        return
    acct = debit_main[0]
    key = f"party:{(rec.party_gstin or rec.party_name).upper()}"
    exists = db.scalar(select(Policy).where(Policy.company_id == company.id, Policy.key == key,
                                            Policy.status.in_(["proposed", "approved"])))
    if exists:
        return
    db.add(Policy(company_id=company.id, key=key, kind="party_account", source="review_correction",
                  definition={"gstin": rec.party_gstin, "party": rec.party_name, "account_code": acct.code},
                  rationale=f"Suggested from a correction on entry #{entry.id}: purchases from {rec.party_name or rec.party_gstin} "
                            f"were booked to {acct.code} {acct.name}. Approve only if this should apply to future invoices.",
                  created_by=user_id))
    db.flush()


def update_record(db: Session, company: Company, rec: SourceRecord, user_id: int, fields: dict) -> JournalEntry | None:
    editable = {"party_name", "party_gstin", "doc_number", "doc_date", "original_doc_number", "note_side",
                "place_of_supply", "taxable_amount", "cgst", "sgst", "igst", "cess", "total_amount", "category_hint",
                "bank_account_code", "description", "reference"}
    if rec.status in {"posted", "approved"}:
        raise WorkflowError("This record has an approved or posted entry; its data can no longer be edited.")
    changes = {}
    for k, v in fields.items():
        if k not in editable:
            continue
        old = getattr(rec, k)
        if v == "":
            v = None
        if old != v:
            setattr(rec, k, v)
            changes[k] = {"from": old, "to": v}
            prov = dict(rec.provenance or {})
            prov[k] = {"source": "user_correction", "user_id": user_id, "confidence": 1.0}
            rec.provenance = prov
    if not changes:
        return None
    if "doc_number" in changes:
        rec.doc_number_norm = norm_doc_number(rec.doc_number)
    if "original_doc_number" in changes or "note_side" in changes:
        rec.original_record_id = None
    log_event(db, company.id, user_id, "record.corrected", "source_record", rec.id, {"changes": changes})
    # re-run duplicate detection for open candidates
    db.query(DuplicateCandidate).filter(DuplicateCandidate.record_id == rec.id,
                                        DuplicateCandidate.status == "open").delete()
    db.flush()
    detect_duplicates(db, rec)
    if rec.status == "rejected":
        rec.status = "extracted"
    return propose_for_record(db, company, rec, user_id)


def approve_entry(db: Session, company: Company, entry: JournalEntry, user_id: int, role: str,
                  acknowledge_review: bool, note: str | None) -> JournalEntry:
    if entry.status not in OPEN_ENTRY:
        raise WorkflowError(f"Only entries awaiting review can be approved; this entry is {entry.status}.")
    results = validate_entry(db, company, entry)
    errors = [r for r in results if r.status == Status.ERROR]
    if errors:
        raise WorkflowError("The entry has errors that must be corrected before approval.", 409,
                            {"errors": [r.message for r in errors]})
    reviews = [r for r in results if r.status == Status.NEEDS_REVIEW]
    if reviews and not acknowledge_review:
        raise WorkflowError("This entry has open review points. Confirm you have reviewed them to approve.", 409,
                            {"review_points": [r.message for r in reviews]})
    if reviews and not (note and note.strip()):
        raise WorkflowError("Add a note explaining why the review points are acceptable.", 422)
    if entry.last_edited_by == user_id and role not in {"owner"}:
        raise WorkflowError("You corrected this entry; another reviewer must approve it.", 403)
    entry.status = "approved"
    entry.approved_by = user_id
    entry.approved_at = utcnow()
    if entry.source_record:
        entry.source_record.status = "approved"
        entry.source_record.review_question = None
    log_event(db, company.id, user_id, "entry.approved", "journal_entry", entry.id,
              {"version": entry.version, "acknowledged_review_points": [r.message for r in reviews], "note": note})
    db.flush()
    return entry


def reject_entry(db: Session, company: Company, entry: JournalEntry, user_id: int, reason: str) -> JournalEntry:
    if entry.status not in OPEN_ENTRY | {"approved"}:
        raise WorkflowError(f"A {entry.status} entry cannot be rejected.")
    if not reason or not reason.strip():
        raise WorkflowError("Give a reason for rejecting the entry.", 422)
    entry.status = "rejected"
    entry.rejection_reason = reason.strip()
    entry.posting_eligible = False
    if entry.source_record and entry.source_record.status not in {"duplicate"}:
        entry.source_record.status = "rejected"
    log_event(db, company.id, user_id, "entry.rejected", "journal_entry", entry.id, {"reason": reason})
    db.flush()
    return entry


def post_entry(db: Session, company: Company, entry: JournalEntry, user_id: int) -> tuple[Posting, bool]:
    """Idempotent: posting an already-posted entry returns the existing posting (created=False)."""
    existing = db.scalar(select(Posting).where(Posting.entry_id == entry.id))
    if existing:
        return existing, False
    if entry.status != "approved":
        raise WorkflowError("Only approved entries can be posted.")
    results = validate_entry(db, company, entry)
    blocking = [r for r in results if r.status == Status.ERROR]
    if blocking:
        raise WorkflowError("The entry no longer passes validation and cannot be posted.", 409,
                            {"errors": [r.message for r in blocking]})
    p = Posting(company_id=company.id, entry_id=entry.id, entry_version=entry.version, posted_by=user_id)
    db.add(p)
    entry.status = "posted"
    if entry.source_record:
        entry.source_record.status = "posted"
    db.flush()
    log_event(db, company.id, user_id, "entry.posted", "journal_entry", entry.id, {"version": entry.version, "posting_id": p.id})
    return p, True


def decide_duplicate(db: Session, company: Company, cand: DuplicateCandidate, user_id: int, decision: str,
                     note: str | None) -> DuplicateCandidate:
    if cand.status != "open":
        raise WorkflowError("This duplicate candidate has already been decided.")
    if decision not in {"confirmed_duplicate", "not_duplicate"}:
        raise WorkflowError("Decision must be confirmed_duplicate or not_duplicate.", 422)
    cand.status = decision
    cand.decided_by = user_id
    cand.decided_at = utcnow()
    cand.decision_note = note
    rec = cand.record
    warning = None
    if decision == "confirmed_duplicate":
        posted = db.scalar(select(JournalEntry).where(JournalEntry.source_record_id == rec.id, JournalEntry.status == "posted"))
        if posted:
            warning = (f"Entry #{posted.id} for this record is already posted. FinV does not delete posted entries; "
                       "record a reversing entry through the normal approval workflow.")
        for e in db.scalars(select(JournalEntry).where(JournalEntry.source_record_id == rec.id,
                                                       JournalEntry.status.in_(OPEN_ENTRY | {"approved"}))).all():
            e.status = "rejected"
            e.rejection_reason = f"Confirmed duplicate of record #{cand.matched_record_id}"
            e.posting_eligible = False
        if not posted:
            rec.status = "duplicate"
        # other open candidates on this record are moot
        for other in db.scalars(select(DuplicateCandidate).where(DuplicateCandidate.record_id == rec.id,
                                                                 DuplicateCandidate.status == "open")).all():
            other.status = "not_duplicate" if other.id != cand.id else other.status
    log_event(db, company.id, user_id, f"duplicate.{decision}", "duplicate_candidate", cand.id,
              {"record_id": rec.id, "matched_record_id": cand.matched_record_id, "note": note, "warning": warning})
    db.flush()
    if decision == "not_duplicate":
        e = db.scalar(select(JournalEntry).where(JournalEntry.source_record_id == rec.id,
                                                 JournalEntry.status.in_(OPEN_ENTRY)))
        if e:
            validate_entry(db, company, e)
    cand.signals = {**(cand.signals or {}), "decision_warning": warning} if warning else cand.signals
    return cand


# ------------------------------------------------------------------ document processing
def store_records(db: Session, company: Company, doc: Document, records: list[dict], bank_account_code: str | None) -> list[SourceRecord]:
    order = {"purchase_invoice": 0, "sales_invoice": 0, "bank_txn": 1, "debit_note": 2, "credit_note": 2}
    out = []
    for r in sorted(records, key=lambda r: (order.get(r["record_type"], 3), r.get("row_index") or 0)):
        rec = SourceRecord(company_id=company.id, document_id=doc.id, row_index=r.get("row_index"),
                           record_type=r["record_type"], note_side=r.get("note_side"), party_name=r.get("party_name"),
                           party_gstin=r.get("party_gstin"), doc_number=r.get("doc_number"),
                           doc_number_norm=r.get("doc_number_norm") or norm_doc_number(r.get("doc_number")),
                           doc_date=r.get("doc_date"), due_date=r.get("due_date"),
                           original_doc_number=r.get("original_doc_number"), place_of_supply=r.get("place_of_supply"),
                           taxable_amount=r.get("taxable"), cgst=r.get("cgst"), sgst=r.get("sgst"), igst=r.get("igst"),
                           cess=r.get("cess"), total_amount=r.get("total"), category_hint=r.get("category_hint"),
                           bank_account_code=bank_account_code if r["record_type"] == "bank_txn" else None,
                           description=r.get("description"), reference=r.get("reference"),
                           debit_amount=r.get("debit_amount"), credit_amount=r.get("credit_amount"),
                           balance=r.get("balance"), line_items=r.get("line_items"),
                           provenance=_jsonable(r.get("provenance")), raw=_jsonable(r.get("raw")))
        rec.content_fingerprint = fingerprint(rec.record_type, rec.party_gstin or rec.party_name, rec.doc_number_norm,
                                              rec.doc_date, rec.total_amount, rec.debit_amount, rec.credit_amount,
                                              rec.reference)
        db.add(rec)
        db.flush()
        out.append(rec)
    return out


def bank_balance_notes(records: list[SourceRecord]) -> list[str]:
    rows = [(r.row_index, r.debit_amount, r.credit_amount, r.balance) for r in sorted(records, key=lambda r: r.row_index or 0)
            if r.record_type == "bank_txn"]
    if not any(b for *_, b in rows):
        return []
    fwd = bank_running_balance(rows)
    rev = bank_running_balance(list(reversed(rows)))
    breaks = fwd if len(fwd) <= len(rev) else rev
    if not breaks:
        return ["Running balance checked: every line agrees with the previous balance."]
    return [f"Running balance does not agree at row {b['row']}: expected {b['expected']}, statement shows {b['stated']}."
            for b in breaks[:10]]


def _jsonable(v):
    import json
    return None if v is None else json.loads(json.dumps(v, default=str))


CONTROL_ROLES = {"bank", "cash", "payables", "receivables", "round_off", "tds_payable", "tds_receivable"}


def set_ledger(db: Session, company: Company, entry: JournalEntry, user_id: int, account_code: str,
               note: str | None) -> JournalEntry:
    """Change the classification ledger of an entry (the non-control, non-tax line), or create the two lines of an
    unclassified bank entry. Goes through correct_entry, so it is versioned, re-validated and logged."""
    chart = chart_for(db, company.id)
    acct = chart.by_code(account_code)
    if acct is None or not acct.active:
        raise WorkflowError(f"There is no active ledger with code {account_code}.", 422)
    if entry.status not in OPEN_ENTRY:
        raise WorkflowError(f"Entry #{entry.id} is {entry.status} and cannot be changed.")
    rec = entry.source_record
    lines = [{"account_id": l.account_id, "debit": l.debit, "credit": l.credit, "description": l.description}
             for l in entry.lines]
    if not lines:
        if not rec or rec.record_type != "bank_txn" or not (rec.debit_amount or rec.credit_amount):
            raise WorkflowError("This entry has no lines; open it to enter them.", 422)
        bank = chart.by_code(rec.bank_account_code) if rec.bank_account_code else chart.by_role("bank")
        if bank is None:
            raise WorkflowError("No single bank ledger is set up for this company.", 422)
        amt = rec.debit_amount or rec.credit_amount
        if rec.debit_amount:
            lines = [{"account_id": acct.id, "debit": amt, "credit": ZERO, "description": rec.description},
                     {"account_id": bank.id, "debit": ZERO, "credit": amt, "description": rec.description}]
        else:
            lines = [{"account_id": bank.id, "debit": amt, "credit": ZERO, "description": rec.description},
                     {"account_id": acct.id, "debit": ZERO, "credit": amt, "description": rec.description}]
    else:
        def role(aid):
            a = chart.by_id(aid)
            return (a.role or "") if a else ""
        main = [i for i, l in enumerate(lines)
                if not (role(l["account_id"]) in CONTROL_ROLES or role(l["account_id"]).startswith(("input_", "output_")))]
        if len(main) != 1:
            raise WorkflowError("FinV cannot tell which line to change in this entry; open it and edit the lines.", 422)
        lines[main[0]]["account_id"] = acct.id
    return correct_entry(db, company, entry, user_id, lines, None, None,
                         note or f"Ledger set to {acct.code} {acct.name} via assistant")
