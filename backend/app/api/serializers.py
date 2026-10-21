"""ORM -> JSON. Money is serialized as strings to preserve exact decimals."""
from __future__ import annotations

from decimal import Decimal


def m(v: Decimal | None) -> str | None:
    return None if v is None else f"{v:.2f}"


def d(v) -> str | None:
    return None if v is None else v.isoformat()


def account(a) -> dict:
    return {"id": a.id, "code": a.code, "name": a.name, "type": a.type, "role": a.role, "is_active": a.is_active}


def company(c, role: str | None = None) -> dict:
    return {"id": c.id, "name": c.name, "gstin": c.gstin, "state_code": c.state_code, "currency": c.currency,
            "organization_id": c.organization_id, "role": role, "services": c.services or ["accounting", "audit"]}


def document(doc) -> dict:
    return {"id": doc.id, "filename": doc.filename, "kind": doc.kind, "workspace": doc.workspace,
            "status": doc.status, "error": doc.error, "size_bytes": doc.size_bytes, "sha256": doc.sha256,
            "record_count": doc.record_count, "notes": doc.extraction_notes or [], "duplicate_of_id": doc.duplicate_of_id,
            "content_type": doc.content_type, "has_file": bool(doc.storage_path), "created_at": d(doc.created_at),
            "purged_at": d(doc.purged_at), "validation": doc.validation}


def record(r, brief: bool = False) -> dict:
    out = {"id": r.id, "record_type": r.record_type, "status": r.status, "party_name": r.party_name,
           "party_gstin": r.party_gstin, "doc_number": r.doc_number, "doc_date": d(r.doc_date),
           "total_amount": m(r.total_amount), "description": r.description, "reference": r.reference,
           "debit_amount": m(r.debit_amount), "credit_amount": m(r.credit_amount), "document_id": r.document_id,
           "review_question": r.review_question}
    if brief:
        return out
    out.update({"note_side": r.note_side, "original_doc_number": r.original_doc_number,
                "original_record_id": r.original_record_id, "place_of_supply": r.place_of_supply,
                "taxable_amount": m(r.taxable_amount), "cgst": m(r.cgst), "sgst": m(r.sgst), "igst": m(r.igst),
                "cess": m(r.cess), "due_date": d(r.due_date), "category_hint": r.category_hint,
                "bank_account_code": r.bank_account_code, "balance": m(r.balance), "row_index": r.row_index,
                "provenance": r.provenance or {}, "raw": r.raw or {}, "line_items": r.line_items,
                "document": document(r.document) if r.document else None})
    return out


def result(v) -> dict:
    return {"rule_id": v.rule_id, "rule_version": v.rule_version, "status": v.status, "message": v.message,
            "evidence": v.evidence or {}}


def entry(e, results=None, accounts_by_id=None) -> dict:
    lines = []
    for l in e.lines:
        a = l.account
        lines.append({"id": l.id, "account_id": l.account_id, "account_code": a.code if a else None,
                      "account_name": a.name if a else None, "debit": m(l.debit), "credit": m(l.credit),
                      "description": l.description})
    dr = sum((l.debit for l in e.lines), Decimal("0"))
    cr = sum((l.credit for l in e.lines), Decimal("0"))
    out = {"id": e.id, "source_record_id": e.source_record_id, "entry_date": d(e.entry_date), "narration": e.narration,
           "treatment": e.treatment, "status": e.status, "posting_eligible": e.posting_eligible, "version": e.version,
           "origin": e.origin, "explanation": e.explanation or {}, "lines": lines, "total_debit": m(dr),
           "total_credit": m(cr), "created_at": d(e.created_at), "updated_at": d(e.updated_at),
           "approved_by": e.approved_by, "approved_at": d(e.approved_at), "rejection_reason": e.rejection_reason,
           "last_edited_by": e.last_edited_by}
    if results is not None:
        out["validation"] = [result(r) for r in results]
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        out["validation_counts"] = counts
    return out


def duplicate(c) -> dict:
    return {"id": c.id, "record_id": c.record_id, "matched_record_id": c.matched_record_id, "kind": c.kind,
            "strength": c.strength, "score": c.score, "signals": c.signals, "explanation": c.explanation,
            "status": c.status, "decided_by": c.decided_by, "decided_at": d(c.decided_at),
            "decision_note": c.decision_note, "created_at": d(c.created_at),
            "record": record(c.record, brief=True) if c.record else None,
            "matched_record": record(c.matched_record, brief=True) if c.matched_record else None}


def policy(p) -> dict:
    return {"id": p.id, "key": p.key, "version": p.version, "kind": p.kind, "definition": p.definition,
            "status": p.status, "source": p.source, "rationale": p.rationale, "effective_from": d(p.effective_from),
            "created_by": p.created_by, "approved_by": p.approved_by, "approved_at": d(p.approved_at),
            "created_at": d(p.created_at)}


def event(ev, users: dict | None = None) -> dict:
    return {"id": ev.id, "action": ev.action, "object_type": ev.object_type, "object_id": ev.object_id,
            "details": ev.details or {}, "user_id": ev.user_id,
            "user_name": (users or {}).get(ev.user_id), "created_at": d(ev.created_at), "hash": ev.hash}


def exception(x, comments=None) -> dict:
    out = {"id": x.id, "import_id": x.import_id, "object_type": x.object_type, "object_id": x.object_id,
           "rule_id": x.rule_id, "severity": x.severity, "title": x.title, "explanation": x.explanation,
           "evidence": x.evidence or {}, "suggested_action": x.suggested_action, "status": x.status,
           "assignee_id": x.assignee_id, "created_at": d(x.created_at), "updated_at": d(x.updated_at)}
    if comments is not None:
        out["history"] = [{"id": c.id, "user_id": c.user_id, "from_status": c.from_status, "to_status": c.to_status,
                           "comment": c.comment, "created_at": d(c.created_at)} for c in comments]
    return out


def tb_import(i) -> dict:
    return {"id": i.id, "kind": i.kind, "document_id": i.document_id, "period_label": i.period_label,
            "period_start": d(i.period_start), "period_end": d(i.period_end), "summary": i.summary or {},
            "created_at": d(i.created_at)}


def ledger_entry(l) -> dict:
    return {"id": l.id, "import_id": l.import_id, "row_index": l.row_index, "entry_date": d(l.entry_date),
            "account_code": l.account_code, "account_name": l.account_name, "voucher_no": l.voucher_no,
            "voucher_type": l.voucher_type, "party": l.party, "reference": l.reference, "narration": l.narration,
            "debit": m(l.debit), "credit": m(l.credit)}


def vouching(v) -> dict:
    return {"id": v.id, "ledger_entry_id": v.ledger_entry_id, "record_id": v.record_id, "result": v.result,
            "outcome": v.outcome, "review_status": v.review_status, "comment": v.comment, "created_at": d(v.created_at)}
