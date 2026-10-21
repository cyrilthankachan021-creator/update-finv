"""Bank reconciliation over stored records. Reproducible: the result is recomputed from stored data and
confirmed manual matches every time."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import reconciliation as rc
from ..engine.money import ZERO
from ..models import BankMatch, Company, CompanySettings, JournalEntry, SourceRecord
from .accounting_service import WorkflowError, chart_for
from .audit_log import log_event


def _bank_lines(db: Session, company: Company, chart) -> list[rc.BankLine]:
    recs = db.scalars(select(SourceRecord).where(SourceRecord.company_id == company.id,
                                                 SourceRecord.record_type == "bank_txn",
                                                 SourceRecord.status.notin_(["duplicate"]))).all()
    out = []
    default_bank = chart.by_role("bank")
    for r in recs:
        amt = r.debit_amount or r.credit_amount
        if not amt or (r.debit_amount and r.credit_amount):
            continue
        acct = chart.by_code(r.bank_account_code) if r.bank_account_code else default_bank
        out.append(rc.BankLine(r.id, r.doc_date, amt, "out" if r.debit_amount else "in", r.description, r.reference,
                               acct.id if acct else None))
    return out


def _book_items(db: Session, company: Company, chart) -> list[rc.BookItem]:
    bank_ids = {a.id for a in chart.all_by_role("bank")}
    entries = db.scalars(select(JournalEntry).where(JournalEntry.company_id == company.id,
                                                    JournalEntry.status.in_(["approved", "posted"]))).all()
    out = []
    for e in entries:
        for l in e.lines:
            if l.account_id in bank_ids:
                amt, direction = (l.debit, "in") if l.debit and l.debit > ZERO else (l.credit, "out")
                src = e.source_record_id if (e.source_record and e.source_record.record_type == "bank_txn") else None
                out.append(rc.BookItem(e.id, e.entry_date, amt, direction, e.narration, src, l.account_id))
    return out


def run(db: Session, company: Company) -> dict:
    chart = chart_for(db, company.id)
    s = db.get(CompanySettings, company.id)
    confirmed = [(m.bank_record_id, m.entry_id) for m in db.scalars(select(BankMatch).where(
        BankMatch.company_id == company.id, BankMatch.status == "confirmed")).all()]
    res = rc.reconcile(_bank_lines(db, company, chart), _book_items(db, company, chart),
                       s.bank_match_window_days if s else 3, confirmed)
    return {"rules_version": rc.RULES_VERSION, "matched": res.matched, "ambiguous": res.ambiguous,
            "unmatched_bank": res.unmatched_bank, "unmatched_book": res.unmatched_book,
            "possible_transfers": res.possible_transfers, "near_misses": res.near_misses}


def manual_match(db: Session, company: Company, bank_record_id: int, entry_id: int, user_id: int) -> BankMatch:
    rec = db.get(SourceRecord, bank_record_id)
    entry = db.get(JournalEntry, entry_id)
    if not rec or rec.company_id != company.id or rec.record_type != "bank_txn":
        raise WorkflowError("Bank line not found.", 404)
    if not entry or entry.company_id != company.id:
        raise WorkflowError("Entry not found.", 404)
    if entry.status not in {"approved", "posted"}:
        raise WorkflowError("Only approved or posted entries can be matched to the bank statement.")
    chart = chart_for(db, company.id)
    items = [i for i in _book_items(db, company, chart) if i.entry_id == entry_id]
    amt = rec.debit_amount or rec.credit_amount
    direction = "out" if rec.debit_amount else "in"
    if not any(i.amount == amt and i.direction == direction for i in items):
        raise WorkflowError("The entry's bank line amount or direction does not equal the statement line. "
                            "Correct the entry first, or record the difference separately.")
    for m in db.scalars(select(BankMatch).where(BankMatch.company_id == company.id, BankMatch.status == "confirmed")):
        if m.bank_record_id == bank_record_id or m.entry_id == entry_id:
            raise WorkflowError("The bank line or the entry is already matched. Remove that match first.")
    m = BankMatch(company_id=company.id, bank_record_id=bank_record_id, entry_id=entry_id, method="manual",
                  status="confirmed", created_by=user_id)
    db.add(m)
    db.flush()
    log_event(db, company.id, user_id, "bank.matched", "bank_match", m.id, {"bank_record_id": bank_record_id, "entry_id": entry_id})
    return m


def confirm_auto(db: Session, company: Company, user_id: int) -> int:
    """Persist the current automatic matches as confirmed (accountant's decision)."""
    res = run(db, company)
    n = 0
    for m in res["matched"]:
        if m["method"] == "auto":
            db.add(BankMatch(company_id=company.id, bank_record_id=m["bank_record_id"], entry_id=m["entry_id"],
                             method="auto", score=m["score"] or 0, signals=m["signals"], status="confirmed",
                             created_by=user_id))
            n += 1
    db.flush()
    log_event(db, company.id, user_id, "bank.auto_matches_confirmed", None, None, {"count": n})
    return n


def remove_match(db: Session, company: Company, match_id: int, user_id: int) -> None:
    m = db.get(BankMatch, match_id)
    if not m or m.company_id != company.id or m.status != "confirmed":
        raise WorkflowError("Match not found.", 404)
    m.status = "removed"
    log_event(db, company.id, user_id, "bank.match_removed", "bank_match", m.id, {})
    db.flush()
