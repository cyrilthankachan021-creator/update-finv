"""Audit checks over imported trial balances, ledgers and vouching links.

Findings are anomalies to investigate, not confirmed errors. Each finding carries its rule id,
severity, a plain-language explanation, evidence and a suggested next action.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import ZERO, fmt, nz, q
from .normalize import norm_doc_number, norm_party, tokens
from .rules import rule

R_TB_TOTAL = rule("TB-001", "1.0", "error", "Trial balance totals agree",
                  "Total debits equal total credits (closing balances, or movements when no closing column).")
R_TB_ROLL = rule("TB-002", "1.0", "error", "Opening + movement = closing",
                 "For each account, opening balance + debits - credits equals the closing balance.")
R_TB_DUP = rule("TB-003", "1.0", "warning", "Duplicate account codes", "Each account code appears once.")
R_TB_MISSING = rule("TB-004", "1.0", "warning", "Missing account details", "Each line has an account code or name.")
R_TB_ABN = rule("TB-005", "1.0", "warning", "Abnormal balance direction",
                "Assets/expenses normally carry debit balances; liabilities/equity/income carry credit balances.")
R_TB_CLASS = rule("TB-006", "1.0", "warning", "Unexpected classification",
                  "Account names that clearly indicate a type (e.g. 'Bank', 'Sales') should be classified accordingly.")
R_TB_MAT = rule("TB-007", "1.0", "warning", "Material movement", "Account movements above materiality are highlighted for review.")
R_LED_DUP = rule("LED-001", "1.0", "warning", "Possible duplicate ledger entries",
                 "Same account, date, amount and voucher/reference appear more than once.")
R_LED_ROUND = rule("LED-002", "1.0", "warning", "Large round-amount entries",
                   "Round amounts at or above materiality are a common audit focus area.")
R_LED_PERIOD = rule("LED-003", "1.0", "warning", "Entries outside the period", "Entry dates fall outside the import period.")
R_LED_TB = rule("LED-004", "1.0", "error", "Ledger agrees with trial balance",
                "Ledger movements per account equal the trial balance movements for the same period.")
R_LED_WEEKEND = rule("LED-005", "1.0", "warning", "Sunday postings", "Entries dated on a Sunday can indicate unusual activity.")
R_VCH_MISSING = rule("VCH-001", "1.0", "warning", "Missing supporting evidence",
                     "Ledger entries at or above the vouching threshold should be linked to evidence.")
R_VCH_DIFF = rule("VCH-002", "1.0", "error", "Evidence agrees with entry",
                  "Amount, date, counterparty and reference of the evidence agree with the ledger entry.")
R_VCH_REUSE = rule("VCH-003", "1.0", "warning", "Evidence used for several entries",
                   "The same supporting document is linked to more than one ledger entry.")

ASSET_WORDS = {"BANK", "CASH", "DEBTORS", "RECEIVABLE", "RECEIVABLES", "INVENTORY", "STOCK", "EQUIPMENT", "MACHINERY", "BUILDING", "FURNITURE"}
LIAB_WORDS = {"CREDITORS", "PAYABLE", "PAYABLES", "LOAN", "BORROWINGS", "OVERDRAFT"}
INCOME_WORDS = {"SALES", "REVENUE", "INCOME"}
EXPENSE_WORDS = {"EXPENSE", "EXPENSES", "RENT", "SALARY", "SALARIES", "WAGES", "PURCHASES", "DEPRECIATION"}
EQUITY_WORDS = {"CAPITAL", "RESERVES", "EQUITY"}


@dataclass
class TBLine:
    id: int
    row: int
    code: str | None
    name: str | None
    type: str | None
    opening: Decimal | None
    debit: Decimal
    credit: Decimal
    closing: Decimal | None


@dataclass
class LedgerLine:
    id: int
    row: int
    date: date | None
    code: str | None
    name: str | None
    voucher: str | None
    party: str | None
    reference: str | None
    narration: str | None
    debit: Decimal
    credit: Decimal


@dataclass
class Finding:
    rule_id: str
    severity: str        # low | medium | high
    title: str
    explanation: str
    object_type: str
    object_id: int | None
    evidence: dict = field(default_factory=dict)
    action: str = ""
    key: str = ""        # stable identity for de-duplication across re-runs


def guess_type(name: str | None) -> str | None:
    t = tokens(name, 3)
    for words, typ in [(ASSET_WORDS, "asset"), (LIAB_WORDS, "liability"), (INCOME_WORDS, "income"),
                       (EXPENSE_WORDS, "expense"), (EQUITY_WORDS, "equity")]:
        if t & words:
            return typ
    return None


def check_trial_balance(lines: list[TBLine], materiality: Decimal) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    has_closing = any(l.closing is not None for l in lines)
    if has_closing:
        tot_dr = sum((l.closing for l in lines if l.closing and l.closing > 0), ZERO)
        tot_cr = -sum((l.closing for l in lines if l.closing and l.closing < 0), ZERO)
        basis = "closing balances"
    else:
        tot_dr = sum((l.debit for l in lines), ZERO)
        tot_cr = sum((l.credit for l in lines), ZERO)
        basis = "debit/credit columns"
    summary = {"lines": len(lines), "total_debit": str(q(tot_dr)), "total_credit": str(q(tot_cr)), "basis": basis,
               "difference": str(q(tot_dr - tot_cr))}
    if tot_dr != tot_cr:
        f.append(Finding(R_TB_TOTAL.id, "high", "Trial balance does not agree",
                         f"Total debits {fmt(tot_dr)} and credits {fmt(tot_cr)} ({basis}) differ by {fmt(tot_dr - tot_cr)}.",
                         "tb_import", None, summary, "Check for missing accounts or mis-keyed balances in the export.", "total"))

    for l in lines:
        if l.opening is not None and l.closing is not None:
            expected = l.opening + l.debit - l.credit
            if expected != l.closing:
                f.append(Finding(R_TB_ROLL.id, "high", f"Balance does not roll forward: {l.name or l.code}",
                                 f"Opening {fmt(l.opening)} + debits {fmt(l.debit)} - credits {fmt(l.credit)} = {fmt(expected)}, "
                                 f"but the closing balance is {fmt(l.closing)}.",
                                 "tb_line", l.id, {"expected": str(expected), "closing": str(l.closing)},
                                 "Obtain the account ledger and agree the movement.", f"roll:{l.code or l.name}"))
        if not (l.code or l.name):
            f.append(Finding(R_TB_MISSING.id, "low", f"Row {l.row} has no account", "The row has neither a code nor a name.",
                             "tb_line", l.id, {}, "Identify the account with the client.", f"missing:{l.row}"))
        bal = l.closing if l.closing is not None else l.debit - l.credit
        typ = l.type or guess_type(l.name)
        if typ and bal:
            abnormal = (typ in {"asset", "expense"} and bal < 0) or (typ in {"liability", "equity", "income"} and bal > 0)
            if abnormal:
                f.append(Finding(R_TB_ABN.id, "medium", f"Abnormal balance: {l.name or l.code}",
                                 f"A{'n' if typ[0] in 'aeiou' else ''} {typ} account with a {'credit' if bal < 0 else 'debit'} balance of {fmt(abs(bal))}.",
                                 "tb_line", l.id, {"type": typ, "balance": str(bal)},
                                 "Confirm the classification or investigate the balance.", f"abn:{l.code or l.name}"))
        guessed = guess_type(l.name)
        if l.type and guessed and guessed != l.type:
            f.append(Finding(R_TB_CLASS.id, "low", f"Classification check: {l.name}",
                             f"The account name suggests {guessed}, but it is classified as {l.type}.",
                             "tb_line", l.id, {"classified": l.type, "name_suggests": guessed},
                             "Confirm the grouping used in the financial statements.", f"class:{l.code or l.name}"))
        movement = l.debit + l.credit
        if materiality and movement >= materiality:
            f.append(Finding(R_TB_MAT.id, "low", f"Material movement: {l.name or l.code}",
                             f"Period movement {fmt(movement)} is at or above materiality {fmt(materiality)}.",
                             "tb_line", l.id, {"movement": str(movement)}, "Include in substantive testing.", f"mat:{l.code or l.name}"))

    seen = defaultdict(list)
    for l in lines:
        if l.code:
            seen[l.code.strip().upper()].append(l.row)
    for code, rows in seen.items():
        if len(rows) > 1:
            f.append(Finding(R_TB_DUP.id, "medium", f"Account code {code} appears {len(rows)} times",
                             f"Rows {', '.join(map(str, rows))} share the same account code.", "tb_import", None,
                             {"rows": rows}, "Check whether balances were duplicated in the export.", f"dup:{code}"))
    return summary, f


def check_ledger(lines: list[LedgerLine], materiality: Decimal, period: tuple[date | None, date | None],
                 tb_movements: dict[str, tuple[Decimal, Decimal]] | None = None) -> list[Finding]:
    f: list[Finding] = []
    groups = defaultdict(list)
    for l in lines:
        key = ((l.code or l.name or "").upper(), l.date, l.debit, l.credit,
               norm_doc_number(l.voucher) or norm_doc_number(l.reference) or "")
        groups[key].append(l)
    for key, ls in groups.items():
        if len(ls) > 1:
            amt = ls[0].debit or ls[0].credit
            f.append(Finding(R_LED_DUP.id, "medium", f"Possible duplicate entries in {ls[0].name or ls[0].code}",
                             f"{len(ls)} entries on {ls[0].date} for {fmt(amt)}"
                             f"{' with voucher ' + (ls[0].voucher or ls[0].reference) if (ls[0].voucher or ls[0].reference) else ''} "
                             "share the same account, date and amount.",
                             "ledger_entry", ls[0].id, {"ledger_entry_ids": [x.id for x in ls]},
                             "Vouch both entries to supporting documents.", f"ldup:{'-'.join(str(x.id) for x in ls)}"))
    start, end = period
    for l in lines:
        amt = l.debit or l.credit
        if materiality and amt >= materiality and amt % 1000 == 0:
            f.append(Finding(R_LED_ROUND.id, "low", f"Large round amount in {l.name or l.code}",
                             f"Entry of {fmt(amt)} on {l.date} is a round amount at or above materiality.",
                             "ledger_entry", l.id, {"amount": str(amt)}, "Vouch to supporting evidence.", f"round:{l.id}"))
        if l.date and ((start and l.date < start) or (end and l.date > end)):
            f.append(Finding(R_LED_PERIOD.id, "medium", "Entry dated outside the period",
                             f"Entry dated {l.date} is outside the period {start} to {end}.", "ledger_entry", l.id,
                             {"date": str(l.date)}, "Check cut-off.", f"period:{l.id}"))
        if l.date and l.date.weekday() == 6:
            f.append(Finding(R_LED_WEEKEND.id, "low", "Sunday posting",
                             f"Entry of {fmt(amt)} in {l.name or l.code} is dated Sunday {l.date}.", "ledger_entry", l.id,
                             {"date": str(l.date)}, "Confirm the entry is genuine.", f"sun:{l.id}"))
    if tb_movements:
        led = defaultdict(lambda: [ZERO, ZERO])
        for l in lines:
            k = (l.code or l.name or "").strip().upper()
            led[k][0] += l.debit
            led[k][1] += l.credit
        for k, (dr, cr) in led.items():
            if k in tb_movements:
                tdr, tcr = tb_movements[k]
                if (dr, cr) != (tdr, tcr):
                    f.append(Finding(R_LED_TB.id, "high", f"Ledger does not agree with trial balance: {k}",
                                     f"Ledger movements Dr {fmt(dr)} / Cr {fmt(cr)} differ from trial balance Dr {fmt(tdr)} / Cr {fmt(tcr)}.",
                                     "account", None, {"account": k, "ledger": [str(dr), str(cr)], "tb": [str(tdr), str(tcr)]},
                                     "Identify missing or extra ledger entries.", f"ledtb:{k}"))
    return f


@dataclass
class Evidence:
    id: int
    record_type: str
    party_name: str | None
    party_gstin: str | None
    doc_number: str | None
    doc_date: date | None
    total: Decimal | None
    reference: str | None = None
    taxes: Decimal | None = None
    taxable: Decimal | None = None
    description: str | None = None


def vouch(entry: LedgerLine, ev: Evidence, date_window_days: int = 7) -> dict:
    """Compare a ledger entry with one piece of evidence. Returns per-check outcomes."""
    checks = []
    amt = entry.debit or entry.credit
    ev_amt = ev.total
    if ev_amt is not None and ev_amt == amt:
        checks.append({"check": "amount", "ok": True, "detail": f"Ledger {fmt(amt)} equals the evidence total"})
    elif ev.taxable is not None and ev.taxable == amt and ev.record_type != "bank_txn":
        checks.append({"check": "amount", "ok": True,
                       "detail": f"Ledger {fmt(amt)} equals the taxable value (the ledger records the amount before GST; "
                                 f"document total {fmt(ev_amt)})"})
    else:
        checks.append({"check": "amount", "ok": False,
                       "detail": f"Ledger {fmt(amt)} vs evidence {fmt(ev_amt)}"
                                 + (f" (taxable value {fmt(ev.taxable)})" if ev.taxable is not None and ev.record_type != "bank_txn" else "")})
    if entry.date and ev.doc_date:
        gap = abs((entry.date - ev.doc_date).days)
        checks.append({"check": "date", "ok": gap <= date_window_days,
                       "detail": f"Ledger {entry.date} vs evidence {ev.doc_date} ({gap} days apart)"})
    else:
        checks.append({"check": "date", "ok": None, "detail": "Date missing on one side"})
    if entry.party or ev.party_name:
        a, b = norm_party(entry.party) or "", norm_party(ev.party_name) or ""
        ok = bool(a and b) and (a == b or a in b or b in a)
        checks.append({"check": "counterparty", "ok": ok if (a and b) else None,
                       "detail": f"Ledger '{entry.party or '—'}' vs evidence '{ev.party_name or '—'}'"})
    refs = {norm_doc_number(x) for x in [entry.voucher, entry.reference] if x}
    refs |= {norm_doc_number(t) for t in (entry.narration or "").replace("/", " ").split()}
    refs.discard(None)
    if ev.record_type == "bank_txn":
        # A bank's own reference (UTR, cheque no.) rarely appears in the books; look for a shared document number
        # in the bank description instead. No overlap is "not checked", not a discrepancy.
        bank_refs = {norm_doc_number(t) for t in f"{ev.description or ''} {ev.reference or ''}".replace("/", " ").split()}
        common = sorted(r for r in refs & bank_refs if r and len(r) >= 3)
        checks.append({"check": "reference", "ok": True if common else None,
                       "detail": f"Bank description mentions {', '.join(common)}" if common
                       else "No common invoice or voucher number between the bank line and the entry"})
    else:
        ev_ref = norm_doc_number(ev.doc_number or ev.reference)
        if ev_ref:
            checks.append({"check": "reference", "ok": ev_ref in refs,
                           "detail": f"Evidence number {ev.doc_number or ev.reference} {'found' if ev_ref in refs else 'not found'} in the voucher, reference or narration"})
    failed = [c for c in checks if c["ok"] is False]
    agreed = [c for c in checks if c["ok"] is True]
    if failed:
        outcome, summary = "discrepancy", "Evidence differs on: " + ", ".join(c["check"] for c in failed) + "."
    elif len(agreed) >= 2:
        outcome, summary = "consistent", "Evidence agrees with the ledger entry on " + ", ".join(c["check"] for c in agreed) + "; nothing compared differs."
    else:
        # Finding nothing wrong is not verification: with fewer than two fields compared, a person has to check it.
        not_checked = [c["check"] for c in checks if c["ok"] is None]
        outcome = "needs_verification"
        summary = (f"Only {len(agreed)} field{'s' if len(agreed) != 1 else ''} could be compared"
                   + (f" ({', '.join(c['check'] for c in agreed)})" if agreed else "")
                   + (f"; not checked: {', '.join(not_checked)}" if not_checked else "") + ". Verify this entry manually.")
    return {"checks": checks, "outcome": outcome, "summary": summary}


def missing_evidence(lines: list[LedgerLine], linked_ids: set[int], threshold: Decimal,
                     bank_like: set[str] | None = None) -> list[Finding]:
    f = []
    for l in lines:
        amt = l.debit or l.credit
        if amt >= threshold and l.id not in linked_ids and amt > 0:
            f.append(Finding(R_VCH_MISSING.id, "medium" if amt >= threshold * 10 and threshold else "low",
                             f"No evidence linked: {l.name or l.code} {fmt(amt)}",
                             f"Entry {l.voucher or ''} dated {l.date} for {fmt(amt)} has no supporting document linked.".replace("  ", " "),
                             "ledger_entry", l.id, {"amount": str(amt)}, "Link the invoice, receipt or bank line that supports it.",
                             f"noev:{l.id}"))
    return f
