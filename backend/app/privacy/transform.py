"""Privacy transformation engine.

Original data -> task-specific minimised context -> pseudonymised identifiers -> outbound gate.

- Only fields the task needs are included (see `build_bank_classification_context`).
- Identifying values are replaced with stable per-task tokens (COMPANY_X, PARTY_1, DOC_1 ...).
  The mapping stays in memory locally and is used to reconstruct the response.
- Long digit runs, e-mail addresses, phone numbers, UPI handles and IFSC-like codes are removed
  from free text.
- Amounts, dates and debit/credit direction are kept unchanged when the task needs them; they are
  not randomised because that would break the accounting meaning. Pseudonymisation is therefore
  NOT anonymisation: amounts and dates together can still identify a transaction.
- `assert_outbound_allowed` is the final gate: it refuses any payload that still contains an
  original sensitive value. It runs on every outbound AI call.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


class PrivacyViolation(RuntimeError):
    """Raised when an outbound payload would contain original sensitive data."""


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_UPI = re.compile(r"\b[\w.-]+@[a-z]{2,}\b", re.IGNORECASE)
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_GSTIN = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_LONG_DIGITS = re.compile(r"\d{4,}")
_PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")


@dataclass
class Pseudonymizer:
    """Per-task, in-memory mapping between original values and tokens."""
    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    sensitive: set[str] = field(default_factory=set)   # originals that must never leave

    def token(self, kind: str, original: str | None) -> str | None:
        if original is None or str(original).strip() == "":
            return None
        key = f"{kind}:{str(original).strip().upper()}"
        if key not in self.forward:
            if kind == "COMPANY":
                tok = "COMPANY_X"
            else:
                self.counters[kind] = self.counters.get(kind, 0) + 1
                tok = f"{kind}_{self.counters[kind]}"
            self.forward[key] = tok
            self.reverse[tok] = str(original)
        self.mark_sensitive(original)
        return self.forward[key]

    def mark_sensitive(self, value: str | None) -> None:
        if value and len(str(value).strip()) >= 3:
            self.sensitive.add(str(value).strip())

    def original(self, tok: str) -> str | None:
        return self.reverse.get(tok)

    def scrub_text(self, text: str | None) -> str | None:
        """Replace known identities with their tokens, then strip generic identifiers."""
        if not text:
            return text
        out = text
        # longest originals first, so "ABC Traders Pvt Ltd" wins over "ABC"
        for key, tok in sorted(self.forward.items(), key=lambda kv: -len(kv[0])):
            orig = self.reverse[tok]
            if len(orig) >= 3:
                out = re.sub(re.escape(orig), tok, out, flags=re.IGNORECASE)
        out = _EMAIL.sub("[EMAIL]", out)
        out = _UPI.sub("[UPI_ID]", out)
        out = _GSTIN.sub("[GSTIN]", out)
        out = _IFSC.sub("[IFSC]", out)
        out = _PAN.sub("[PAN]", out)
        out = _PHONE.sub("[NUMBER]", out)
        out = _LONG_DIGITS.sub("[NUMBER]", out)
        return re.sub(r"\s+", " ", out).strip()


def assert_outbound_allowed(payload: dict, p: Pseudonymizer) -> None:
    """Final outbound gate. Refuses payloads containing original sensitive values or raw identifiers."""
    blob = json.dumps(payload, default=str)
    upper = blob.upper()
    leaked = sorted(v for v in p.sensitive if v.upper() in upper)
    if leaked:
        raise PrivacyViolation(f"Outbound AI payload blocked: it contains {len(leaked)} original identifying value(s).")
    for name, rx in (("GSTIN", _GSTIN), ("email address", _EMAIL), ("PAN", _PAN)):
        if rx.search(blob):
            raise PrivacyViolation(f"Outbound AI payload blocked: it contains a raw {name}.")


def build_bank_classification_context(record, company_name: str | None, chart_accounts: list, known_parties: list[str],
                                      p: Pseudonymizer) -> dict:
    """Minimised context for classifying ONE bank line.

    Included: direction, amount, month, scrubbed description, candidate ledger list (code/name/type).
    Excluded: bank account numbers, references, balances, other transactions, GSTINs, document files.
    """
    if company_name:
        p.token("COMPANY", company_name)
    for name in known_parties:
        p.token("PARTY", name)
    p.mark_sensitive(record.reference)
    direction = "money_out" if record.debit_amount and not record.credit_amount else "money_in"
    amount = record.debit_amount or record.credit_amount
    candidates = [{"code": a.code, "name": p.scrub_text(a.name), "type": a.type}
                  for a in chart_accounts if a.is_active and a.role not in {"bank"}]
    return {
        "task": "classify_bank_transaction",
        "record_ref": "TXN_1",
        "transaction": {
            "direction": direction,
            "amount": str(amount) if amount is not None else None,
            "month": record.doc_date.strftime("%Y-%m") if record.doc_date else None,
            "description": p.scrub_text(record.description),
        },
        "candidate_accounts": candidates,
    }
