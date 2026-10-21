"""Bank reconciliation: match bank statement lines to ledger entries on the bank account.

Deterministic and reproducible: same inputs -> same output. Amount equality (to the paisa) and
direction are required for any automatic match; date proximity, reference and party tokens add
score. When two candidates tie, nothing is auto-matched and the line is reported as ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .normalize import norm_doc_number, tokens

RULES_VERSION = "1.0"


@dataclass
class BankLine:
    id: int
    date: date | None
    amount: Decimal
    direction: str   # in | out
    description: str | None = None
    reference: str | None = None
    bank_account_id: int | None = None


@dataclass
class BookItem:
    entry_id: int
    date: date | None
    amount: Decimal
    direction: str   # in (bank debited) | out (bank credited)
    narration: str | None = None
    source_record_id: int | None = None
    bank_account_id: int | None = None


@dataclass
class ReconResult:
    matched: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    unmatched_bank: list[int] = field(default_factory=list)
    unmatched_book: list[int] = field(default_factory=list)
    possible_transfers: list[dict] = field(default_factory=list)
    near_misses: list[dict] = field(default_factory=list)


def score(b: BankLine, e: BookItem, window_days: int) -> tuple[int, dict] | None:
    if b.amount != e.amount or b.direction != e.direction:
        return None
    if b.bank_account_id and e.bank_account_id and b.bank_account_id != e.bank_account_id:
        return None
    signals: dict = {"amount": True}
    s = 50
    if e.source_record_id is not None and e.source_record_id == b.id:
        return 100, {"amount": True, "entry_created_from_this_line": True}
    if b.date and e.date:
        gap = abs((b.date - e.date).days)
        if gap > window_days:
            return None
        signals["date_gap_days"] = gap
        s += 20 - min(gap, 10) * 2
    ref = norm_doc_number(b.reference) if b.reference else None
    narr = f"{e.narration or ''}"
    if ref and len(ref) >= 3 and ref in {norm_doc_number(t) for t in narr.split()}:
        signals["reference_in_narration"] = True
        s += 20
    common = tokens(b.description, 4) & tokens(narr, 4)
    if common:
        signals["shared_words"] = sorted(common)[:5]
        s += min(10, 3 * len(common))
    return s, signals


def reconcile(bank: list[BankLine], book: list[BookItem], window_days: int = 3,
              confirmed: list[tuple[int, int]] | None = None) -> ReconResult:
    res = ReconResult()
    confirmed = confirmed or []
    used_b = {b for b, _ in confirmed}
    used_e = {e for _, e in confirmed}
    for b_id, e_id in confirmed:
        res.matched.append({"bank_record_id": b_id, "entry_id": e_id, "method": "confirmed", "score": None, "signals": {}})

    open_b = [b for b in bank if b.id not in used_b]
    open_e = [e for e in book if e.entry_id not in used_e]
    candidates: dict[int, list[tuple[int, int, dict]]] = {}
    for b in open_b:
        for e in open_e:
            sc = score(b, e, window_days)
            if sc:
                candidates.setdefault(b.id, []).append((sc[0], e.entry_id, sc[1]))

    # Greedy by best score, in a deterministic order; ties -> ambiguous.
    flat = sorted(((c[0], b_id, c[1], c[2]) for b_id, cs in candidates.items() for c in cs),
                  key=lambda t: (-t[0], t[1], t[2]))
    taken_b, taken_e, ambiguous_b = set(), set(), set()
    for sc, b_id, e_id, sig in flat:
        if b_id in taken_b or e_id in taken_e or b_id in ambiguous_b:
            continue
        rivals = [c for c in candidates[b_id] if c[1] not in taken_e and c[0] == sc and c[1] != e_id]
        rival_lines = [bb for bb, cs in candidates.items() if bb != b_id and bb not in taken_b
                       and any(c[1] == e_id and c[0] == sc for c in cs)]
        if rivals or rival_lines:
            ambiguous_b.add(b_id)
            res.ambiguous.append({"bank_record_id": b_id,
                                  "candidate_entry_ids": sorted({e_id, *[r[1] for r in rivals]}),
                                  "competing_bank_record_ids": sorted(rival_lines),
                                  "reason": "Several entries match this line equally well; match it manually."})
            continue
        taken_b.add(b_id)
        taken_e.add(e_id)
        res.matched.append({"bank_record_id": b_id, "entry_id": e_id, "method": "auto", "score": sc, "signals": sig})

    res.unmatched_bank = sorted(b.id for b in open_b if b.id not in taken_b)
    res.unmatched_book = sorted(e.entry_id for e in open_e if e.entry_id not in taken_e)

    # Near misses: same direction and date window, small amount difference (for explanation only)
    for b in open_b:
        if b.id in taken_b or b.id in ambiguous_b:
            continue
        for e in open_e:
            if e.entry_id in taken_e or e.direction != b.direction or not (b.date and e.date):
                continue
            if abs((b.date - e.date).days) <= window_days and b.amount != e.amount:
                diff = b.amount - e.amount
                if abs(diff) <= max(Decimal("1.00"), b.amount * Decimal("0.01")):
                    res.near_misses.append({"bank_record_id": b.id, "entry_id": e.entry_id, "difference": str(diff)})

    # Possible transfers between the company's own bank accounts
    unmatched = [b for b in open_b if b.id in set(res.unmatched_bank)]
    for i, a in enumerate(unmatched):
        for c in unmatched[i + 1:]:
            if (a.bank_account_id and c.bank_account_id and a.bank_account_id != c.bank_account_id
                    and a.amount == c.amount and a.direction != c.direction and a.date and c.date
                    and abs((a.date - c.date).days) <= 2):
                res.possible_transfers.append({"bank_record_ids": [a.id, c.id], "amount": str(a.amount)})
    return res
