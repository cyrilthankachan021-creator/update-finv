"""Deterministic accounting engine.

Input: structured record data + company context (chart, periods, approved policies, related
records). Output: a proposed treatment with journal lines and an evidence-based explanation,
and validation results for any (proposed or edited) entry.

This module has no knowledge of the database, HTTP, documents or AI providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import gst
from .money import ZERO, fmt, has_more_than_2dp, nz, q
from .normalize import norm_doc_number, norm_party, tokens
from .rules import BLOCKING, RuleResult, Status, result, rule, worst

# ------------------------------------------------------------------ rules
R_BAL = rule("ACC-001", "1.0", "error", "Debits equal credits",
             "Total debits must equal total credits to the paisa.")
R_LINES = rule("ACC-002", "1.0", "error", "Well-formed journal lines",
               "At least two lines; each line has exactly one positive debit or credit amount.")
R_ACCT = rule("ACC-003", "1.0", "error", "Valid ledger accounts",
              "Every line uses an active ledger account from this company's chart of accounts.")
R_PERIOD = rule("ACC-004", "1.0", "error", "Entry date and accounting period",
                "The entry has a date inside a defined, unlocked accounting period.")
R_PREC = rule("ACC-005", "1.0", "error", "Amount precision",
              "Amounts have at most two decimal places.")
R_DOC = rule("ACC-006", "1.0", "review", "Supporting document",
             "Entries should be linked to a source record (invoice, note or bank line).")
R_EVID = rule("ACC-007", "1.0", "error", "Entry agrees with its evidence",
              "Entry amounts, tax lines and control accounts must agree with the source document.")
R_DUPPOST = rule("ACC-008", "1.0", "error", "No duplicate posting",
                 "A source record may only have one approved or posted entry.")
R_DUPCAND = rule("ACC-009", "1.0", "review", "Open duplicate candidates",
                 "Possible duplicates of the source record must be resolved before approval.")
R_CLASS = rule("ACC-010", "1.0", "review", "Classification resolved",
               "The accounting treatment must be supported by evidence; open questions need an answer.")
R_NOTE_REF = rule("NOTE-001", "1.0", "review", "Note references an original invoice",
                  "A credit/debit note should cite the invoice it adjusts.")
R_NOTE_ORIG = rule("NOTE-002", "1.0", "review", "Original invoice found",
                   "The referenced original invoice exists in this company's records.")
R_NOTE_PARTY = rule("NOTE-003", "1.0", "error", "Note party matches original",
                    "The note's party (GSTIN or name) matches the original invoice.")
R_NOTE_LIMIT = rule("NOTE-004", "1.0", "error", "Credit notes do not exceed the invoice",
                    "Total credit notes against an invoice must not exceed the invoice value.")
R_NOTE_TAX = rule("NOTE-005", "1.0", "review", "Note tax heads match original",
                  "A note normally uses the same tax heads (CGST/SGST vs IGST) as the original invoice.")
R_TDS = rule("TDS-001", "1.0", "review", "TDS consideration",
             "Where an approved company TDS policy covers the expense, the entry should account for TDS; thresholds and section must be confirmed.")

TAX_ROLES = {
    "purchase": {"cgst": "input_cgst", "sgst": "input_sgst", "igst": "input_igst", "cess": "input_cess"},
    "sales": {"cgst": "output_cgst", "sgst": "output_sgst", "igst": "output_igst", "cess": "output_cess"},
}


# ------------------------------------------------------------------ data
@dataclass
class AccountRef:
    id: int
    code: str
    name: str
    type: str
    role: str | None = None
    active: bool = True


@dataclass
class Chart:
    accounts: list[AccountRef]

    def by_role(self, role: str) -> AccountRef | None:
        found = [a for a in self.accounts if a.role == role and a.active]
        return found[0] if len(found) == 1 else None

    def all_by_role(self, role: str) -> list[AccountRef]:
        return [a for a in self.accounts if a.role == role and a.active]

    def by_code(self, code: str | None) -> AccountRef | None:
        if not code:
            return None
        return next((a for a in self.accounts if a.code == str(code).strip()), None)

    def by_id(self, id: int | None) -> AccountRef | None:
        return next((a for a in self.accounts if a.id == id), None)


@dataclass
class Period:
    name: str
    start: date
    end: date
    locked: bool = False


@dataclass
class RecordData:
    id: int | None
    record_type: str
    note_side: str | None = None
    party_name: str | None = None
    party_gstin: str | None = None
    doc_number: str | None = None
    doc_date: date | None = None
    original_doc_number: str | None = None
    place_of_supply: str | None = None
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    cess: Decimal | None = None
    total: Decimal | None = None
    category_hint: str | None = None
    bank_account_code: str | None = None
    description: str | None = None
    reference: str | None = None
    debit: Decimal | None = None    # bank: money out
    credit: Decimal | None = None   # bank: money in

    @property
    def bank_amount(self) -> Decimal | None:
        if self.debit and not self.credit:
            return self.debit
        if self.credit and not self.debit:
            return self.credit
        return None

    @property
    def bank_direction(self) -> str | None:
        if self.debit and not self.credit:
            return "out"
        if self.credit and not self.debit:
            return "in"
        return None


@dataclass
class LineData:
    account_id: int | None
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str | None = None


@dataclass
class OpenItem:
    """An invoice that a bank line could settle."""
    record: RecordData
    outstanding: Decimal


@dataclass
class Context:
    chart: Chart
    periods: list[Period] = field(default_factory=list)
    company_state: str | None = None
    policies: list[dict] = field(default_factory=list)     # approved policies only
    original: RecordData | None = None                      # note -> original invoice
    prior_notes_total: Decimal = ZERO                       # other credit notes against same original
    open_items: list[OpenItem] = field(default_factory=list)
    other_active_entries: int = 0                           # approved/posted entries for same source
    open_duplicates: list[dict] = field(default_factory=list)
    ai_suggestion: dict | None = None                       # already validated locally


@dataclass
class Proposal:
    treatment: str
    lines: list[LineData]
    explanation: dict
    question: str | None = None
    origin: str = "engine"


# ------------------------------------------------------------------ helpers
def _line(acct: AccountRef | None, debit=ZERO, credit=ZERO, desc=None) -> LineData:
    return LineData(acct.id if acct else None, q(debit), q(credit), desc)


def _ev(field_name: str, value, source="document") -> dict:
    return {"field": field_name, "value": None if value is None else str(value), "source": source}


def resolve_note_side(rec: RecordData, original: RecordData | None) -> str | None:
    if rec.note_side in {"sales", "purchase"}:
        return rec.note_side
    if original is not None:
        return "purchase" if original.record_type == "purchase_invoice" else "sales"
    return None


def _policy_account(rec: RecordData, ctx: Context) -> tuple[AccountRef | None, dict | None]:
    """Approved party->account policy (by GSTIN first, then normalised party name)."""
    pg, pn = (rec.party_gstin or "").upper(), norm_party(rec.party_name)
    for p in ctx.policies:
        if p["kind"] != "party_account":
            continue
        d = p["definition"]
        if (d.get("gstin") and d["gstin"].upper() == pg) or (d.get("party") and norm_party(d["party"]) == pn and pn):
            acct = ctx.chart.by_code(d.get("account_code"))
            if acct:
                return acct, p
    return None, None


def _hint_account(rec: RecordData, ctx: Context) -> AccountRef | None:
    if not rec.category_hint:
        return None
    hint = rec.category_hint.strip()
    acct = ctx.chart.by_code(hint)
    if acct:
        return acct
    hn = hint.lower()
    matches = [a for a in ctx.chart.accounts if a.active and a.name.lower() == hn]
    return matches[0] if len(matches) == 1 else None


def _tds_policy(rec: RecordData, acct: AccountRef | None, ctx: Context) -> dict | None:
    for p in ctx.policies:
        if p["kind"] != "tds_category":
            continue
        d = p["definition"]
        if acct and d.get("account_code") == acct.code:
            return p
        if d.get("party") and norm_party(d["party"]) == norm_party(rec.party_name):
            return p
    return None


# ------------------------------------------------------------------ proposals
def propose(rec: RecordData, ctx: Context) -> Proposal:
    if rec.record_type in {"purchase_invoice", "sales_invoice", "credit_note", "debit_note"}:
        return _propose_tax_document(rec, ctx)
    if rec.record_type == "bank_txn":
        return _propose_bank(rec, ctx)
    return Proposal("unknown", [], {"summary": "FinV does not know how to account for this record type."},
                    question="Select the accounting treatment for this record.")


def _propose_tax_document(rec: RecordData, ctx: Context) -> Proposal:
    chart = ctx.chart
    evidence = [_ev("party_name", rec.party_name), _ev("doc_number", rec.doc_number),
                _ev("doc_date", rec.doc_date), _ev("taxable_amount", rec.taxable), _ev("total_amount", rec.total)]
    for h in ("cgst", "sgst", "igst", "cess"):
        if getattr(rec, h):
            evidence.append(_ev(h, getattr(rec, h)))
    uncertainties: list[str] = []
    accounts: list[dict] = []
    question = None

    if rec.total is None:
        return Proposal(rec.record_type, [], {
            "summary": "The document total could not be read, so no entry was proposed.",
            "evidence": evidence, "uncertainties": ["Total amount missing."],
            "next_action": "Enter the total from the original document."},
            question="What is the total amount on this document?")

    taxes = {h: nz(getattr(rec, h)) for h in ("cgst", "sgst", "igst", "cess")}
    tax_total = sum(taxes.values(), ZERO)
    taxable = rec.taxable
    if taxable is None:
        taxable = rec.total - tax_total
        uncertainties.append(f"Taxable value was not on the document; FinV derived it as total minus taxes ({fmt(taxable)}).")
    round_off = q(rec.total - taxable - tax_total)

    if rec.record_type in {"credit_note", "debit_note"}:
        side = resolve_note_side(rec, ctx.original)
        if side is None:
            return Proposal(rec.record_type, [], {
                "summary": f"FinV identified a {rec.record_type.replace('_', ' ')} but cannot tell whether it relates to a sale or a purchase.",
                "evidence": evidence,
                "uncertainties": ["No original invoice was found and the note does not say whether it was issued or received."],
                "next_action": "Choose whether this note adjusts a sale (issued to a customer) or a purchase (received from a supplier)."},
                question="Does this note adjust a sale (issued to a customer) or a purchase (received from a supplier)?")
    else:
        side = "purchase" if rec.record_type == "purchase_invoice" else "sales"

    # Counter account (purchases/sales or returns)
    main_reason = ""
    policy_used = None
    if rec.record_type == "purchase_invoice" or (rec.record_type == "debit_note" and side == "purchase"):
        main, policy_used = _policy_account(rec, ctx)
        if main:
            main_reason = f"Approved company policy '{policy_used['key']}' v{policy_used['version']} maps this supplier to {main.name}."
        else:
            main = _hint_account(rec, ctx)
            if main:
                main_reason = f"The document's category '{rec.category_hint}' matches ledger {main.name}."
            else:
                main = chart.by_role("purchases")
                main_reason = "Defaulted to the Purchases ledger."
                question = "Confirm the ledger for this purchase: goods for resale, an expense, or a fixed asset?"
                uncertainties.append("No policy or category identifies whether this is stock, an expense or an asset.")
    elif rec.record_type == "sales_invoice" or (rec.record_type == "debit_note" and side == "sales"):
        main = chart.by_role("sales")
        main_reason = "Sales invoices and customer debit notes credit the Sales ledger."
    elif side == "sales":  # credit note issued to customer
        main = chart.by_role("sales_returns") or chart.by_role("sales")
        main_reason = "A credit note issued to a customer reduces sales."
    else:  # credit note received from supplier
        main = chart.by_role("purchase_returns") or chart.by_role("purchases")
        main_reason = "A credit note received from a supplier reduces purchases."

    control = chart.by_role("payables" if side == "purchase" else "receivables")
    tax_accts = {h: chart.by_role(TAX_ROLES[side][h]) for h in taxes}
    round_acct = chart.by_role("round_off")
    party = rec.party_name or "party"

    # Direction: does this document increase (normal) or decrease (reversal) the side?
    reverse = rec.record_type == "credit_note"
    lines: list[LineData] = []

    def dr_cr(acct, amount, debit_normal: bool, desc):
        if amount == ZERO:
            return
        is_debit = debit_normal != reverse
        if amount < 0:
            is_debit, amount = not is_debit, -amount
        lines.append(_line(acct, debit=amount if is_debit else ZERO, credit=ZERO if is_debit else amount, desc=desc))

    purchase_like = side == "purchase"
    dr_cr(main, taxable, purchase_like, f"{rec.doc_number or ''} {party}".strip())
    for h, amt in taxes.items():
        dr_cr(tax_accts[h], amt, purchase_like, f"{h.upper()} {'input' if purchase_like else 'output'}")
    if round_off != ZERO:
        dr_cr(round_acct, round_off, purchase_like, "Round-off")
    dr_cr(control, rec.total, not purchase_like, party)

    if main:
        accounts.append({"code": main.code, "name": main.name, "reason": main_reason})
    if control:
        accounts.append({"code": control.code, "name": control.name,
                         "reason": f"{'Supplier' if purchase_like else 'Customer'} balance for {party}."})
    for h, a in tax_accts.items():
        if taxes[h] and a:
            accounts.append({"code": a.code, "name": a.name, "reason": f"{h.upper()} shown on the document."})
    missing_roles = [n for n, a in [("main ledger", main), ("control account", control)] if a is None]
    missing_roles += [f"{h.upper()} ledger" for h, a in tax_accts.items() if taxes[h] and a is None]
    if round_off and round_acct is None:
        missing_roles.append("round-off ledger")
    if missing_roles:
        uncertainties.append("The chart of accounts has no unique ledger for: " + ", ".join(missing_roles) + ".")
        question = question or "Choose ledgers for the lines without an account."

    kind = rec.record_type.replace("_", " ")
    summary = (f"FinV identified a {'sales' if side == 'sales' else 'purchase'}-side {kind} "
               f"{rec.doc_number or '(no number)'} from {party} for {fmt(rec.total)}.")
    if rec.record_type in {"credit_note", "debit_note"} and ctx.original:
        summary += f" It references invoice {ctx.original.doc_number}."
    if rec.record_type == "debit_note" and side == "purchase":
        uncertainties.append("Treated as a supplier's debit note (additional charge). If you issued this note to the supplier for a return, change the treatment.")

    # TDS: only from approved company policy; FinV ships no statutory rates.
    tds = _tds_policy(rec, main, ctx) if purchase_like and rec.record_type == "purchase_invoice" else None
    if tds:
        rate = Decimal(str(tds["definition"].get("rate_percent", "0")))
        tds_acct = chart.by_role("tds_payable")
        if rate > 0 and tds_acct and control:
            tds_amt = q(taxable * rate / 100)
            lines.append(_line(control, debit=tds_amt, desc=f"TDS deducted ({tds['definition'].get('section_label', 'TDS')})"))
            lines.append(_line(tds_acct, credit=tds_amt, desc=f"TDS payable ({tds['definition'].get('section_label', 'TDS')})"))
            accounts.append({"code": tds_acct.code, "name": tds_acct.name,
                             "reason": f"Approved TDS policy '{tds['key']}' applies {rate}% on the taxable value."})
        uncertainties.append("Confirm the TDS section and that the threshold for this payee is crossed.")

    return Proposal(rec.record_type, lines, {
        "summary": summary,
        "accounts": accounts,
        "evidence": evidence,
        "uncertainties": uncertainties,
        "next_action": question or "Review the proposed lines and approve if they match the document.",
        "policy": {"key": policy_used["key"], "version": policy_used["version"]} if policy_used else None,
    }, question=question)


def _propose_bank(rec: RecordData, ctx: Context) -> Proposal:
    chart = ctx.chart
    amount, direction = rec.bank_amount, rec.bank_direction
    evidence = [_ev("date", rec.doc_date, "bank statement"), _ev("description", rec.description, "bank statement"),
                _ev("reference", rec.reference, "bank statement"), _ev("debit", rec.debit, "bank statement"),
                _ev("credit", rec.credit, "bank statement")]
    if amount is None:
        return Proposal("bank_unknown", [], {
            "summary": "The bank line has no single debit or credit amount.", "evidence": evidence,
            "uncertainties": ["Debit and credit are both empty or both filled."],
            "next_action": "Check the statement line."}, question="Correct the debit/credit amount for this bank line.")

    bank = chart.by_code(rec.bank_account_code) if rec.bank_account_code else chart.by_role("bank")
    if bank is None:
        return Proposal("bank_unknown", [], {
            "summary": "FinV could not determine which bank ledger this statement belongs to.", "evidence": evidence,
            "uncertainties": ["The chart has no single bank ledger and the upload did not specify one."],
            "next_action": "Select the bank ledger for this statement."},
            question="Which bank ledger does this statement belong to?")

    words = "money paid out" if direction == "out" else "money received"
    hay = f"{rec.description or ''} {rec.reference or ''}"
    parts = hay.replace("/", " ").replace(":", " ").split()
    hay_norm = {norm_doc_number(t) for t in parts} | {norm_doc_number(a + b) for a, b in zip(parts, parts[1:])}
    hay_tokens = tokens(hay)

    def lines_for(counter: AccountRef | None, desc: str) -> list[LineData]:
        if direction == "out":
            return [_line(counter, debit=amount, desc=desc), _line(bank, credit=amount, desc=rec.description)]
        return [_line(bank, debit=amount, desc=rec.description), _line(counter, credit=amount, desc=desc)]

    # 1. Invoice reference match
    wanted = "purchase_invoice" if direction == "out" else "sales_invoice"
    ref_hits = [oi for oi in ctx.open_items if oi.record.record_type == wanted and oi.record.doc_number
                and len(norm_doc_number(oi.record.doc_number) or "") >= 3
                and norm_doc_number(oi.record.doc_number) in hay_norm]
    name_hits = [oi for oi in ctx.open_items if oi.record.record_type == wanted and oi.outstanding == amount
                 and (tokens(oi.record.party_name) & hay_tokens)]
    hit, how, conflict = None, None, None
    if len(ref_hits) == 1:
        hit, how = ref_hits[0], "reference"
    elif len(ref_hits) > 1:
        exact = [oi for oi in ref_hits if oi.outstanding == amount]
        if len(exact) == 1:
            hit, how = exact[0], "reference"
            conflict = [oi.record.id for oi in ref_hits if oi is not exact[0]]
    elif len(name_hits) == 1:
        hit, how = name_hits[0], "party_amount"
    if hit:
        control = chart.by_role("payables" if direction == "out" else "receivables")
        inv = hit.record
        kind = "supplier payment" if direction == "out" else "customer receipt"
        uncertainties, question = [], None
        if how == "reference":
            reason = f"The bank {'reference' if rec.reference else 'description'} contains invoice number {inv.doc_number}"
        else:
            reason = f"The description mentions {inv.party_name} and the amount equals the outstanding balance of invoice {inv.doc_number}"
            uncertainties.append("Matched on party name and amount only.")
            question = f"Confirm this {words} settles invoice {inv.doc_number}."
        if conflict:
            uncertainties.append(f"Other records with the same invoice number exist ({', '.join('#' + str(i) for i in conflict)}); "
                                 "resolve that inconsistency first.")
            question = f"Confirm this {words} settles invoice {inv.doc_number} (record #{inv.id})."
        if amount != hit.outstanding:
            uncertainties.append(f"Amount {fmt(amount)} differs from the invoice outstanding {fmt(hit.outstanding)} (part-payment or difference).")
            question = question or f"Confirm whether this is a part-payment of invoice {inv.doc_number}."
        summary = (f"FinV classified this {words} as a {kind} because {reason}. "
                   f"The proposed entry {'clears the supplier payable' if direction == 'out' else 'reduces the customer receivable'}.")
        return Proposal(f"bank_{'supplier_payment' if direction == 'out' else 'customer_receipt'}",
                        lines_for(control, f"{inv.party_name} {inv.doc_number}"), {
            "summary": summary,
            "accounts": [{"code": bank.code, "name": bank.name, "reason": "Statement bank ledger."}] +
                        ([{"code": control.code, "name": control.name, "reason": reason + "."}] if control else []),
            "evidence": evidence + [_ev("matched_invoice", inv.doc_number, f"record #{inv.id}"),
                                    _ev("invoice_outstanding", hit.outstanding, f"record #{inv.id}")],
            "matched_record_id": inv.id,
            "uncertainties": uncertainties,
            "next_action": question or "Confirm the invoice match and approve.",
        }, question=question)

    # 2. Approved company keyword policies
    for p in ctx.policies:
        if p["kind"] != "bank_keyword":
            continue
        d = p["definition"]
        if d.get("direction") and d["direction"] != direction:
            continue
        kw = (d.get("keyword") or "").strip().upper()
        if kw and kw in (hay or "").upper():
            acct = chart.by_code(d.get("account_code"))
            if acct is None:
                continue
            reason = f"Approved company policy '{p['key']}' v{p['version']} maps descriptions containing '{d['keyword']}' to {acct.name}."
            return Proposal("bank_policy", lines_for(acct, d.get("keyword")), {
                "summary": f"FinV classified this {words} using an approved company policy.",
                "accounts": [{"code": bank.code, "name": bank.name, "reason": "Statement bank ledger."},
                             {"code": acct.code, "name": acct.name, "reason": reason}],
                "evidence": evidence, "uncertainties": [],
                "policy": {"key": p["key"], "version": p["version"]},
                "next_action": "Review and approve.",
            })

    # 3. AI suggestion (already schema-validated and mapped back locally). Always needs review.
    if ctx.ai_suggestion:
        acct = chart.by_code(ctx.ai_suggestion.get("account_code"))
        if acct:
            return Proposal("bank_ai_suggested", lines_for(acct, ctx.ai_suggestion.get("category")), {
                "summary": f"An AI suggestion classified this {words} as '{ctx.ai_suggestion.get('category')}'. "
                           "This is a suggestion only; FinV could not confirm it from records.",
                "accounts": [{"code": bank.code, "name": bank.name, "reason": "Statement bank ledger."},
                             {"code": acct.code, "name": acct.name,
                              "reason": ctx.ai_suggestion.get("rationale") or "AI suggestion."}],
                "evidence": evidence, "uncertainties": ["Classification is an AI suggestion based on a minimised description."],
                "next_action": "Confirm or change the ledger before approval.",
            }, question=f"Confirm the ledger for this {words}.", origin="ai_assisted")

    # 4. Ask — never infer economic meaning from direction alone.
    options = ("a sales receipt, loan received, capital introduced, refund or a transfer" if direction == "in"
               else "a supplier payment, expense, asset purchase, loan repayment, tax payment or a transfer")
    return Proposal("bank_unclassified", [], {
        "summary": f"FinV cannot determine whether this bank {'credit' if direction == 'in' else 'debit'} of {fmt(amount)} is {options}.",
        "evidence": evidence,
        "uncertainties": ["No matching invoice, approved policy or confirmed suggestion."],
        "next_action": "Select the correct ledger to continue.",
    }, question="Select the ledger for this bank transaction.")


# ------------------------------------------------------------------ validation
def validate(lines: list[LineData], entry_date: date | None, rec: RecordData | None, ctx: Context,
             question: str | None = None) -> list[RuleResult]:
    out: list[RuleResult] = []
    chart = ctx.chart
    dr = sum((l.debit for l in lines), ZERO)
    cr = sum((l.credit for l in lines), ZERO)

    # ACC-002
    bad = [i + 1 for i, l in enumerate(lines)
           if l.debit < 0 or l.credit < 0 or (l.debit > 0) == (l.credit > 0)]
    if not lines and question:
        out.append(result(R_LINES, Status.NEEDS_REVIEW,
                          "No journal lines yet: answer the open question to create them.", line_count=0))
    elif len(lines) < 2:
        out.append(result(R_LINES, Status.ERROR, "An entry needs at least two lines.", line_count=len(lines)))
    elif bad:
        out.append(result(R_LINES, Status.ERROR,
                          f"Line(s) {', '.join(map(str, bad))} must have either a debit or a credit amount, not both or neither.",
                          lines=bad))
    else:
        out.append(result(R_LINES, Status.PASS, "All lines have a single debit or credit amount."))

    # ACC-005
    imprecise = [i + 1 for i, l in enumerate(lines) if has_more_than_2dp(l.debit) or has_more_than_2dp(l.credit)]
    out.append(result(R_PREC, Status.ERROR if imprecise else Status.PASS,
                      f"Line(s) {', '.join(map(str, imprecise))} have more than two decimal places." if imprecise
                      else "All amounts have at most two decimal places.", lines=imprecise))

    # ACC-001
    if lines:
        diff = dr - cr
        out.append(result(R_BAL, Status.PASS if diff == 0 else Status.ERROR,
                          f"Debits {fmt(dr)} equal credits {fmt(cr)}." if diff == 0
                          else f"Debits {fmt(dr)} do not equal credits {fmt(cr)} (difference {fmt(diff)}).",
                          debits=str(dr), credits=str(cr)))

    # ACC-003
    problems = []
    for i, l in enumerate(lines, 1):
        a = chart.by_id(l.account_id)
        if a is None:
            problems.append(f"line {i} has no valid ledger")
        elif not a.active:
            problems.append(f"line {i} uses inactive ledger {a.code} {a.name}")
    out.append(result(R_ACCT, Status.ERROR if problems else Status.PASS,
                      ("Ledger problem: " + "; ".join(problems) + ".") if problems else "All lines use active ledgers.",
                      problems=problems))

    # ACC-004
    if entry_date is None:
        out.append(result(R_PERIOD, Status.ERROR, "The entry has no date."))
    else:
        p = next((p for p in ctx.periods if p.start <= entry_date <= p.end), None)
        if p is None:
            out.append(result(R_PERIOD, Status.NEEDS_REVIEW,
                              f"No accounting period is defined for {entry_date.isoformat()}. Add the period in Settings.",
                              date=entry_date.isoformat()))
        elif p.locked:
            out.append(result(R_PERIOD, Status.ERROR, f"Period {p.name} is locked; entries dated {entry_date.isoformat()} cannot be posted.",
                              period=p.name))
        else:
            out.append(result(R_PERIOD, Status.PASS, f"Date falls in open period {p.name}.", period=p.name))

    # ACC-006
    out.append(result(R_DOC, Status.PASS if rec else Status.NEEDS_REVIEW,
                      "Linked to its source record." if rec else "No supporting document is linked to this entry."))

    # ACC-008 / ACC-009
    out.append(result(R_DUPPOST, Status.ERROR if ctx.other_active_entries else Status.PASS,
                      "Another approved or posted entry already exists for this source record." if ctx.other_active_entries
                      else "No other approved or posted entry for this source record."))
    if ctx.open_duplicates:
        out.append(result(R_DUPCAND, Status.NEEDS_REVIEW,
                          f"{len(ctx.open_duplicates)} possible duplicate(s) of the source record are unresolved. Review them before approval.",
                          candidates=ctx.open_duplicates))
    else:
        out.append(result(R_DUPCAND, Status.PASS, "No unresolved duplicate candidates."))

    # ACC-010
    out.append(result(R_CLASS, Status.NEEDS_REVIEW if question else Status.PASS,
                      question or "Accounting treatment is supported by the recorded evidence."))

    if rec is not None:
        out.extend(_evidence_checks(lines, rec, ctx))
        if rec.record_type in {"purchase_invoice", "sales_invoice", "credit_note", "debit_note"}:
            side = (resolve_note_side(rec, ctx.original) if rec.record_type in {"credit_note", "debit_note"}
                    else "purchase" if rec.record_type == "purchase_invoice" else "sales")
            out.extend(gst.check_tax_document(gst.TaxDoc(
                rec.record_type, side, rec.party_gstin, rec.place_of_supply, rec.taxable, rec.cgst, rec.sgst,
                rec.igst, rec.cess, rec.total, ctx.company_state)))
        if rec.record_type in {"credit_note", "debit_note"}:
            out.extend(check_note(rec, ctx))
        if rec.record_type == "purchase_invoice":
            out.extend(_tds_check(lines, rec, ctx))
    return out


def _evidence_checks(lines: list[LineData], rec: RecordData, ctx: Context) -> list[RuleResult]:
    chart = ctx.chart
    if not lines:
        return []
    issues: list[str] = []
    roles = {l.account_id: (chart.by_id(l.account_id).role if chart.by_id(l.account_id) else None) for l in lines}

    def role_total(role_names: set[str], col: str) -> Decimal:
        return sum((getattr(l, col) for l in lines if roles.get(l.account_id) in role_names), ZERO)

    if rec.record_type == "bank_txn":
        bank_roles = {"bank"}
        bank_ids = {a.id for a in chart.all_by_role("bank")}
        if rec.bank_account_code and chart.by_code(rec.bank_account_code):
            bank_ids = {chart.by_code(rec.bank_account_code).id}
        col = "credit" if rec.bank_direction == "out" else "debit"
        bank_amt = sum((getattr(l, col) for l in lines if l.account_id in bank_ids), ZERO)
        if rec.bank_amount is not None and bank_amt != rec.bank_amount:
            issues.append(f"The bank ledger {col} is {fmt(bank_amt)} but the statement shows {fmt(rec.bank_amount)} "
                          f"{'paid out' if rec.bank_direction == 'out' else 'received'}")
        del bank_roles
    else:
        side = (resolve_note_side(rec, ctx.original) if rec.record_type in {"credit_note", "debit_note"}
                else "purchase" if rec.record_type == "purchase_invoice" else "sales")
        if side and rec.total is not None:
            reverse = rec.record_type == "credit_note"
            control_role = "payables" if side == "purchase" else "receivables"
            # natural column for the control account on this document
            control_col = ("credit" if side == "purchase" else "debit")
            if reverse:
                control_col = "debit" if control_col == "credit" else "credit"
            other_col = "debit" if control_col == "credit" else "credit"
            control_amt = role_total({control_role}, control_col) - role_total({control_role}, other_col)
            tds_amt = role_total({"tds_payable"}, "credit")
            if control_amt + tds_amt != rec.total:
                issues.append(f"The {control_role} {control_col} ({fmt(control_amt + tds_amt)} incl. TDS) does not equal the document total {fmt(rec.total)}")
            tax_col = "debit" if (side == "purchase") != reverse else "credit"
            for h in ("cgst", "sgst", "igst", "cess"):
                doc_amt = nz(getattr(rec, h))
                booked = role_total({TAX_ROLES[side][h]}, tax_col)
                if booked != doc_amt:
                    issues.append(f"{h.upper()} booked {fmt(booked)} but the document shows {fmt(doc_amt)}")
    if issues:
        return [result(R_EVID, Status.ERROR, "The entry does not agree with its source document: " + "; ".join(issues) + ".",
                       issues=issues)]
    return [result(R_EVID, Status.PASS, "Entry amounts, tax lines and control account agree with the source document.")]


def check_note(rec: RecordData, ctx: Context) -> list[RuleResult]:
    out = []
    if not rec.original_doc_number:
        out.append(result(R_NOTE_REF, Status.NEEDS_REVIEW,
                          "The note does not reference an original invoice. Add the invoice number or confirm it is a standalone adjustment."))
        return out
    out.append(result(R_NOTE_REF, Status.PASS, f"References invoice {rec.original_doc_number}."))
    o = ctx.original
    if o is None:
        out.append(result(R_NOTE_ORIG, Status.NEEDS_REVIEW,
                          f"Original invoice {rec.original_doc_number} was not found in this company's records.",
                          original_doc_number=rec.original_doc_number))
        return out
    out.append(result(R_NOTE_ORIG, Status.PASS, f"Original invoice {o.doc_number} found (record #{o.id}).", original_record_id=o.id))
    if rec.party_gstin and o.party_gstin:
        same = rec.party_gstin.upper() == o.party_gstin.upper()
        detail = f"note GSTIN {rec.party_gstin}, invoice GSTIN {o.party_gstin}"
    else:
        same = bool(norm_party(rec.party_name)) and norm_party(rec.party_name) == norm_party(o.party_name)
        detail = f"note party '{rec.party_name}', invoice party '{o.party_name}'"
    out.append(result(R_NOTE_PARTY, Status.PASS if same else Status.ERROR,
                      f"Party matches the original invoice ({detail})." if same
                      else f"The note's party differs from the original invoice ({detail})."))
    if rec.record_type == "credit_note" and rec.total is not None and o.total is not None:
        cumulative = ctx.prior_notes_total + rec.total
        ok = cumulative <= o.total
        out.append(result(R_NOTE_LIMIT, Status.PASS if ok else Status.ERROR,
                          f"Credit notes against {o.doc_number} total {fmt(cumulative)} of invoice value {fmt(o.total)}." if ok
                          else f"Credit notes against {o.doc_number} would total {fmt(cumulative)}, exceeding the invoice value {fmt(o.total)}.",
                          invoice_total=str(o.total), prior_notes=str(ctx.prior_notes_total), this_note=str(rec.total)))
    note_intra, orig_intra = bool(nz(rec.cgst) or nz(rec.sgst)), bool(nz(o.cgst) or nz(o.sgst))
    note_inter, orig_inter = bool(nz(rec.igst)), bool(nz(o.igst))
    if (note_intra or note_inter) and (orig_intra or orig_inter):
        same_heads = note_intra == orig_intra and note_inter == orig_inter
        out.append(result(R_NOTE_TAX, Status.PASS if same_heads else Status.NEEDS_REVIEW,
                          "Tax heads match the original invoice." if same_heads
                          else "The note uses different tax heads from the original invoice (CGST/SGST vs IGST)."))
    return out


def _tds_check(lines: list[LineData], rec: RecordData, ctx: Context) -> list[RuleResult]:
    main_ids = [l.account_id for l in lines if l.debit > 0]
    accts = [ctx.chart.by_id(i) for i in main_ids]
    pol = None
    for a in accts:
        pol = _tds_policy(rec, a, ctx)
        if pol:
            break
    if not pol:
        return [result(R_TDS, Status.NOT_APPLICABLE, "No approved company TDS policy covers this expense.")]
    tds_ids = {a.id for a in ctx.chart.all_by_role("tds_payable")}
    has_tds = any(l.account_id in tds_ids and l.credit > 0 for l in lines)
    label = pol["definition"].get("section_label", "TDS")
    if not has_tds:
        return [result(R_TDS, Status.NEEDS_REVIEW,
                       f"Approved policy '{pol['key']}' indicates TDS ({label}) may apply, but the entry has no TDS line.")]
    return [result(R_TDS, Status.NEEDS_REVIEW,
                   f"TDS ({label}) is recorded under approved policy '{pol['key']}'. Confirm the section and that the payee's threshold is crossed.")]


def is_posting_eligible(results: list[RuleResult]) -> bool:
    return not any(r.status in BLOCKING for r in results)


def has_errors(results: list[RuleResult]) -> bool:
    return any(r.status == Status.ERROR for r in results)


__all__ = ["propose", "validate", "is_posting_eligible", "has_errors", "worst", "Chart", "AccountRef", "Context",
           "RecordData", "LineData", "Period", "OpenItem", "Proposal", "resolve_note_side", "check_note"]
