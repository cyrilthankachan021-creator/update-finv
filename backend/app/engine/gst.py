"""India GST checks (versioned, deterministic).

Scope and limits (also in ACCOUNTING_RULES.md):
- GSTIN checks are format + checksum only. They do not prove a GSTIN is registered or active;
  no live GSTN verification service is integrated.
- Place-of-supply logic applies the general rule (supplier state == place of supply ->
  CGST+SGST, otherwise IGST). Special cases (SEZ, exports, specific services, etc.) are not
  modelled, so a mismatch is reported as "needs review", never as a confirmed error.
- Rate plausibility only checks the implied rate against a configured list of slab rates.
  It does not determine the correct rate for any goods or service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .money import ZERO, fmt, nz, q
from .rules import Status, rule, result, RuleResult

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
VALID_STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}

# Rates seen on Indian GST invoices (percent, CGST+SGST or IGST combined). Plausibility only.
GST_SLAB_RATES_VERSION = "2025-10"
GST_SLAB_RATES = [Decimal(x) for x in ("0", "0.1", "0.25", "1.5", "3", "5", "6", "12", "18", "28", "40")]

R_GSTIN = rule("GST-001", "1.0", "review", "GSTIN format and checksum",
               "Checks the party GSTIN structure, state code and check digit. Does not confirm registration status.")
R_ARITH = rule("GST-002", "1.0", "error", "Invoice arithmetic",
               "Taxable value + CGST + SGST + IGST + cess must equal the invoice total (round-off up to 1.00 is a warning).")
R_STRUCT = rule("GST-003", "1.0", "error", "Tax structure",
                "An invoice charges either CGST+SGST (equal amounts) or IGST, not both.")
R_POS = rule("GST-004", "1.0", "review", "Place of supply consistency",
             "Under the general rule, intra-state supplies carry CGST+SGST and inter-state supplies carry IGST. Missing data or a mismatch needs review.")
R_RATE = rule("GST-005", "1.0", "warning", "Implied GST rate plausibility",
              "The implied rate (tax / taxable value) should be one of the configured slab rates.")
R_ITC = rule("GST-006", "1.0", "review", "Input tax credit support",
             "Input tax on a purchase needs a valid supplier GSTIN to support an input tax credit claim.")


def gstin_check_digit(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _CHARS[(36 - total % 36) % 36]


def validate_gstin(gstin: str | None) -> tuple[bool, str]:
    if not gstin:
        return False, "No GSTIN provided."
    g = gstin.strip().upper()
    if len(g) != 15:
        return False, f"GSTIN must have 15 characters; '{g}' has {len(g)}."
    if not GSTIN_RE.match(g):
        return False, "GSTIN does not follow the standard structure (state code, PAN, entity code, 'Z', check digit)."
    if g[:2] not in VALID_STATE_CODES:
        return False, f"GSTIN state code {g[:2]} is not a valid state code."
    expected = gstin_check_digit(g[:14])
    if g[14] != expected:
        return False, f"GSTIN check digit is {g[14]} but should be {expected}; the number may be mistyped."
    return True, "GSTIN structure and check digit are valid (registration status not verified)."


@dataclass
class TaxDoc:
    record_type: str            # purchase_invoice | sales_invoice | credit_note | debit_note
    side: str | None            # purchase | sales
    party_gstin: str | None
    place_of_supply: str | None
    taxable: Decimal | None
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    cess: Decimal | None
    total: Decimal | None
    company_state: str | None


def check_tax_document(d: TaxDoc) -> list[RuleResult]:
    out: list[RuleResult] = []
    cgst, sgst, igst, cess = nz(d.cgst), nz(d.sgst), nz(d.igst), nz(d.cess)
    tax = cgst + sgst + igst
    has_tax = tax != ZERO or cess != ZERO

    # GST-001
    if d.party_gstin:
        ok, msg = validate_gstin(d.party_gstin)
        out.append(result(R_GSTIN, Status.PASS if ok else Status.NEEDS_REVIEW, msg, gstin=d.party_gstin))
    else:
        out.append(result(R_GSTIN, Status.NOT_APPLICABLE, "No party GSTIN on the document."))

    # GST-002
    if d.total is None or d.taxable is None:
        out.append(result(R_ARITH, Status.NEEDS_REVIEW,
                          "Taxable value or total is missing, so the invoice arithmetic cannot be checked.",
                          taxable=_s(d.taxable), total=_s(d.total)))
    else:
        computed = d.taxable + tax + cess
        diff = q(d.total - computed)
        ev = dict(taxable=_s(d.taxable), cgst=_s(cgst), sgst=_s(sgst), igst=_s(igst), cess=_s(cess),
                  computed_total=_s(computed), document_total=_s(d.total), difference=_s(diff))
        if diff == ZERO:
            out.append(result(R_ARITH, Status.PASS, "Taxable value plus taxes equals the document total.", **ev))
        elif abs(diff) <= Decimal("1.00"):
            out.append(result(R_ARITH, Status.WARNING,
                              f"Total differs from taxable value plus taxes by {fmt(diff)}, which looks like round-off.", **ev))
        else:
            out.append(result(R_ARITH, Status.ERROR,
                              f"Total {fmt(d.total)} does not equal taxable value plus taxes {fmt(computed)} "
                              f"(difference {fmt(diff)}). Check the extracted amounts against the document.", **ev))

    # GST-003
    if not has_tax:
        out.append(result(R_STRUCT, Status.NOT_APPLICABLE, "No GST charged on this document."))
    elif (cgst or sgst) and igst:
        out.append(result(R_STRUCT, Status.ERROR, "Both CGST/SGST and IGST are charged on the same document.",
                          cgst=_s(cgst), sgst=_s(sgst), igst=_s(igst)))
    elif (cgst or sgst) and cgst != sgst:
        out.append(result(R_STRUCT, Status.ERROR, f"CGST ({fmt(cgst)}) and SGST ({fmt(sgst)}) should be equal.",
                          cgst=_s(cgst), sgst=_s(sgst)))
    else:
        out.append(result(R_STRUCT, Status.PASS, "Tax heads are consistent."))

    # GST-004
    if tax == ZERO:
        out.append(result(R_POS, Status.NOT_APPLICABLE, "No CGST/SGST/IGST charged."))
    else:
        supplier_state = (d.party_gstin or "")[:2] if d.side == "purchase" else d.company_state
        pos = d.place_of_supply
        if not pos or not supplier_state:
            missing = "place of supply" if not pos else "supplier state"
            out.append(result(R_POS, Status.NEEDS_REVIEW,
                              f"The entry may balance, but the GST treatment cannot be verified because the {missing} is missing.",
                              place_of_supply=pos, supplier_state=supplier_state))
        else:
            intra = supplier_state == pos
            charged_intra = bool(cgst or sgst)
            if intra == charged_intra:
                out.append(result(R_POS, Status.PASS,
                                  f"{'Intra' if intra else 'Inter'}-state supply (supplier state {supplier_state}, place of supply {pos}) "
                                  f"carries {'CGST+SGST' if intra else 'IGST'} as expected under the general rule.",
                                  place_of_supply=pos, supplier_state=supplier_state))
            else:
                out.append(result(R_POS, Status.NEEDS_REVIEW,
                                  f"Supplier state {supplier_state} and place of supply {pos} suggest "
                                  f"{'CGST+SGST' if intra else 'IGST'}, but the document charges "
                                  f"{'CGST+SGST' if charged_intra else 'IGST'}. Special cases (e.g. SEZ supplies) can explain this; confirm.",
                                  place_of_supply=pos, supplier_state=supplier_state))

    # GST-005
    if tax == ZERO or not d.taxable:
        out.append(result(R_RATE, Status.NOT_APPLICABLE, "No rate to check."))
    else:
        rate = (tax / d.taxable * 100)
        near = min(GST_SLAB_RATES, key=lambda s: abs(s - rate))
        ev = dict(implied_rate=f"{rate:.2f}", nearest_slab=str(near), slab_list_version=GST_SLAB_RATES_VERSION)
        if abs(near - rate) <= Decimal("0.05"):
            out.append(result(R_RATE, Status.PASS, f"Implied GST rate {rate:.2f}% matches a known slab.", **ev))
        else:
            out.append(result(R_RATE, Status.WARNING,
                              f"Implied GST rate {rate:.2f}% is not a standard slab (nearest {near}%). "
                              "The document may contain several rates, or an amount may be wrong.", **ev))

    # GST-006
    if d.side == "purchase" and d.record_type in {"purchase_invoice", "debit_note"} and has_tax:
        ok, _ = validate_gstin(d.party_gstin)
        if ok:
            out.append(result(R_ITC, Status.PASS, "Supplier GSTIN is structurally valid for the input tax claim."))
        else:
            out.append(result(R_ITC, Status.NEEDS_REVIEW,
                              "Input tax is recorded but the supplier GSTIN is missing or invalid, so the input tax credit is not supported by this document."))
    return out


def _s(d: Decimal | None) -> str | None:
    return None if d is None else str(q(d))
