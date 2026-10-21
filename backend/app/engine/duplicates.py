"""Duplicate detection and document-integrity comparison.

Strength levels:
- exact:    the same document/transaction by its identifying keys (same party + type + normalised
            number + amounts, or same bank date/amount/direction/reference).
- probable: identifying keys agree except one (e.g. number differs by one character) or bank
            lines agree on date/amount/description without a reference.
- possible: only weak signals agree (same party, amount and date with different numbers). This is
            shown for review because recurring transactions are legitimate.

A same-number document whose amounts/date/party differ is reported as a
"possible_alteration" (document inconsistency), never as fraud.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import fmt, nz
from .normalize import levenshtein, norm_doc_number, norm_party, norm_text

DOC_TYPES = {"purchase_invoice", "sales_invoice", "credit_note", "debit_note"}
COMPARE_FIELDS = ["party_gstin", "party_name", "doc_date", "taxable", "cgst", "sgst", "igst", "cess", "total"]


@dataclass
class DupRecord:
    id: int
    record_type: str
    party_name: str | None = None
    party_gstin: str | None = None
    doc_number: str | None = None
    doc_date: date | None = None
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    cess: Decimal | None = None
    total: Decimal | None = None
    description: str | None = None
    reference: str | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    document_id: int | None = None
    file_sha256: str | None = None
    line_items: list | None = None


@dataclass
class DupMatch:
    record_id: int
    matched_id: int
    kind: str          # duplicate | possible_alteration
    strength: str      # exact | probable | possible
    score: int
    signals: dict = field(default_factory=dict)
    explanation: str = ""
    action: str = ""


def _party_key(r: DupRecord) -> tuple[str, str] | None:
    if r.party_gstin:
        return ("gstin", r.party_gstin.strip().upper())
    p = norm_party(r.party_name)
    return ("name", p) if p else None


def _same_party(a: DupRecord, b: DupRecord) -> bool:
    if a.party_gstin and b.party_gstin:
        return a.party_gstin.strip().upper() == b.party_gstin.strip().upper()
    pa, pb = norm_party(a.party_name), norm_party(b.party_name)
    return bool(pa) and pa == pb


def compare_fields(a: DupRecord, b: DupRecord) -> list[dict]:
    """Field-level differences between two records believed to be the same document."""
    diffs = []
    for f in COMPARE_FIELDS:
        va, vb = getattr(a, f), getattr(b, f)
        if f == "party_name":
            if norm_party(va) != norm_party(vb) and va and vb:
                diffs.append({"field": f, "new": va, "existing": vb})
            continue
        if f in {"cgst", "sgst", "igst", "cess"}:
            va, vb = nz(va), nz(vb)
        if va is None or vb is None:
            continue
        if va != vb:
            diffs.append({"field": f, "new": str(va), "existing": str(vb)})
    return diffs


def line_item_check(r: DupRecord) -> dict | None:
    """If line items were extracted, their amounts should add up to the taxable value."""
    if not r.line_items or r.taxable is None:
        return None
    try:
        total = sum((Decimal(str(li.get("amount"))) for li in r.line_items if li.get("amount") is not None), Decimal("0"))
    except Exception:
        return {"ok": False, "message": "Line item amounts could not be read."}
    if total == r.taxable:
        return {"ok": True, "message": "Line items add up to the taxable value."}
    return {"ok": False, "message": f"Line items add up to {fmt(total)} but the taxable value is {fmt(r.taxable)}.",
            "line_total": str(total), "taxable": str(r.taxable)}


def find_matches(new: DupRecord, existing: list[DupRecord]) -> list[DupMatch]:
    out: list[DupMatch] = []
    for old in existing:
        if old.id == new.id:
            continue
        m = _match_bank(new, old) if new.record_type == "bank_txn" else _match_doc(new, old)
        if m:
            out.append(m)
    order = {"exact": 0, "probable": 1, "possible": 2}
    out.sort(key=lambda m: (order[m.strength], -m.score))
    return out


def _match_doc(new: DupRecord, old: DupRecord) -> DupMatch | None:
    if new.record_type not in DOC_TYPES or old.record_type != new.record_type:
        return None
    if not _same_party(new, old):
        return None
    n_new, n_old = norm_doc_number(new.doc_number), norm_doc_number(old.doc_number)
    signals = {"same_party": True, "document_type": new.record_type}
    label = new.record_type.replace("_", " ")
    if new.file_sha256 and new.file_sha256 == old.file_sha256 and new.document_id != old.document_id:
        signals["identical_file"] = True

    if n_new and n_new == n_old:
        signals["same_number"] = True
        signals["raw_numbers"] = [new.doc_number, old.doc_number]
        diffs = compare_fields(new, old)
        if not diffs:
            same_fmt = (new.doc_number or "").strip() == (old.doc_number or "").strip()
            return DupMatch(new.id, old.id, "duplicate", "exact", 100, signals,
                            f"Duplicate {label}. The {'supplier' if 'purchase' in new.record_type else 'party'} "
                            f"{'GSTIN' if new.party_gstin else 'name'}, {label} number "
                            f"{'' if same_fmt else f'({new.doc_number} vs {old.doc_number}, same after normalising) '}"
                            f"and amounts match record #{old.id}.",
                            "Review both records before posting. Confirm the duplicate so it is not posted twice.")
        signals["differences"] = diffs
        fields = ", ".join(d["field"].replace("_", " ") for d in diffs)
        return DupMatch(new.id, old.id, "possible_alteration", "exact", 90, signals,
                        f"Document inconsistency detected. {label.capitalize()} {new.doc_number} from the same party already exists "
                        f"(record #{old.id}) but these fields differ: {fields}. One copy may have been revised or altered.",
                        "Compare both copies with the party's original document. Keep the correct one and mark the other.")

    same_total = new.total is not None and new.total == old.total
    same_date = new.doc_date is not None and new.doc_date == old.doc_date
    if same_total and n_new and n_old and levenshtein(n_new, n_old) == 1:
        signals.update(same_total=True, same_date=same_date, number_distance=1,
                       raw_numbers=[new.doc_number, old.doc_number])
        return DupMatch(new.id, old.id, "duplicate", "probable", 75, signals,
                        f"Probable duplicate {label}: same party and total {fmt(new.total)}; numbers {new.doc_number} and "
                        f"{old.doc_number} differ by one character, which may be a typing error.",
                        "Check whether both documents are genuine before posting.")
    if same_total and same_date:
        signals.update(same_total=True, same_date=True, raw_numbers=[new.doc_number, old.doc_number])
        return DupMatch(new.id, old.id, "duplicate", "possible", 40, signals,
                        f"Same party, date and total ({fmt(new.total)}) as record #{old.id}, but a different number. "
                        "This can be a legitimate recurring transaction.",
                        "No action needed if these are separate transactions; mark them as not duplicates.")
    return None


def _match_bank(new: DupRecord, old: DupRecord) -> DupMatch | None:
    if old.record_type != "bank_txn":
        return None
    amt_new = (new.debit, new.credit)
    if amt_new != (old.debit, old.credit) or new.doc_date != old.doc_date:
        return None
    signals = {"same_date": True, "same_amount": True,
               "direction": "out" if new.debit else "in", "same_statement": new.document_id == old.document_id}
    ref_new, ref_old = norm_text(new.reference), norm_text(old.reference)
    desc_same = norm_text(new.description) == norm_text(old.description)
    amount = new.debit or new.credit
    if ref_new and ref_new == ref_old:
        signals["same_reference"] = True
        return DupMatch(new.id, old.id, "duplicate", "exact", 100, signals,
                        f"Duplicate bank transaction: date, amount {fmt(amount)} and reference {new.reference} match record #{old.id}"
                        f"{' in the same statement' if signals['same_statement'] else ' from another statement upload'}.",
                        "Overlapping statements are common. Confirm the duplicate so it is not accounted twice.")
    if ref_new and ref_old and ref_new != ref_old:
        return None  # different references: separate transactions
    if desc_same:
        signals["same_description"] = True
        return DupMatch(new.id, old.id, "duplicate", "probable", 70, signals,
                        f"Probable duplicate bank transaction: same date, amount {fmt(amount)} and description as record #{old.id}, "
                        "and no reference to tell them apart.",
                        "Check the bank statement; two identical charges on one day can be genuine.")
    return None


def bank_running_balance(rows: list[tuple[int, Decimal | None, Decimal | None, Decimal | None]]) -> list[dict]:
    """rows: (row_index, debit, credit, balance) in statement order. Returns breaks in the running balance."""
    breaks = []
    prev = None
    for idx, dr, cr, bal in rows:
        if bal is None:
            prev = None
            continue
        if prev is not None:
            expected = prev - nz(dr) + nz(cr)
            if expected != bal:
                breaks.append({"row": idx, "expected": str(expected), "stated": str(bal),
                               "difference": str(bal - expected)})
        prev = bal
    return breaks
