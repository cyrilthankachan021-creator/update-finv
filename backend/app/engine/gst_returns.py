"""GSTR-1 and GSTR-3B calculation from approved/posted records (versioned, deterministic).

Scope and limits (shown to the reviewer as warnings, never hidden):
- One GST rate per document: the rate is inferred from tax / taxable value and must match a known slab.
  Multi-rate invoices need line-level data FinV does not hold, so they block preparation until resolved.
- GSTR-1 Table 12 (HSN summary) needs HSN codes, which FinV does not extract yet. Table 13 (documents issued),
  exports, SEZ, nil-rated detail (Table 8) and advances (Tables 11A/11B) are not prepared.
- GSTR-3B: FinV fills 3.1(a), 3.1(c), 3.2 and 4(A)(5) only. Reverse charge (3.1(d)), ineligible ITC (4(B)),
  interest and late fee are left for the portal. ITC is not matched against GSTR-2B here.
- Tax payable is shown per head before cross-utilisation of credit; the portal performs the actual set-off.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .gst import GST_SLAB_RATES, validate_gstin
from .money import ZERO, nz, q

RULES_VERSION = "gstr-2026.10"
RETURN_TYPES = ("GSTR1", "GSTR3B")
B2CL_LIMIT = Decimal("100000")   # inter-state B2C invoices above this go to B2CL (limit from 1 Aug 2024)
HEADS = ("taxable", "igst", "cgst", "sgst", "cess")


@dataclass
class DocRow:
    """A document in the tax period, with the status of its journal entry."""
    record_id: int
    record_type: str            # sales_invoice | purchase_invoice | credit_note | debit_note
    note_side: str | None       # for notes: sales | purchase
    status: str                 # record status
    entry_id: int | None
    entry_version: int | None
    entry_status: str | None
    party_gstin: str | None
    doc_number: str | None
    doc_date: date | None
    place_of_supply: str | None
    taxable: Decimal | None
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    cess: Decimal | None
    total: Decimal | None
    original_record_id: int | None = None

    @property
    def side(self) -> str:
        if self.record_type == "sales_invoice":
            return "sales"
        if self.record_type == "purchase_invoice":
            return "purchase"
        return self.note_side or "unknown"

    @property
    def included(self) -> bool:
        return self.entry_status in {"approved", "posted"} and self.status not in {"duplicate", "rejected"}


@dataclass
class ReturnResult:
    totals: dict
    breakdown: dict
    warnings: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    record_ids: list = field(default_factory=list)

    @property
    def blocking(self) -> list:
        return [w for w in self.warnings if w["severity"] == "blocking"]


def period_bounds(period: str) -> tuple[date, date]:
    y, m = (int(x) for x in period.split("-"))
    end = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, 1), date.fromordinal(end.toordinal() - 1)


def portal_period(period: str) -> str:
    """'2025-06' -> '062025', the return period format the GST portal uses."""
    y, m = period.split("-")
    return f"{m}{y}"


def _amt(d: Decimal) -> float:
    return float(q(d))


def _s(d: Decimal) -> str:
    return str(q(d))


def _rate(r: DocRow) -> Decimal | None:
    tax = nz(r.cgst) + nz(r.sgst) + nz(r.igst)
    if not r.taxable:
        return None
    rate = tax / r.taxable * 100
    near = min(GST_SLAB_RATES, key=lambda s: abs(s - rate))
    return near if abs(near - rate) <= Decimal("0.05") else None


def _warn(code: str, severity: str, message: str, records: list[DocRow] | None = None) -> dict:
    w = {"code": code, "severity": severity, "message": message}
    if records:
        w["record_ids"] = sorted(r.record_id for r in records)
        w["count"] = len(records)
    return w


def fingerprint(gstin: str, return_type: str, period: str, rows: list[DocRow], undated: list[int]) -> str:
    """Changes whenever any source record or entry for the period changes (amounts, status, entry version)."""
    body = [gstin, return_type, period, RULES_VERSION, sorted(undated)] + sorted(
        [r.record_id, r.record_type, r.note_side, r.status, r.entry_id, r.entry_version, r.entry_status,
         r.party_gstin, r.doc_number, str(r.doc_date), r.place_of_supply, r.original_record_id,
         *(None if v is None else _s(v) for v in (r.taxable, r.cgst, r.sgst, r.igst, r.cess, r.total))]
        for r in rows)
    return hashlib.sha256(json.dumps(body, default=str).encode()).hexdigest()


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _common_warnings(rows: list[DocRow], undated: list[int], sides: set[str]) -> list[dict]:
    out = []
    pending = [r for r in rows if not r.included and r.status not in {"duplicate", "rejected"} and r.side in sides]
    if pending:
        out.append(_warn("PENDING_DOCS", "blocking",
                         f"{len(pending)} document(s) dated in this period are not approved yet, so they are left out. "
                         "Approve or reject them first.", pending))
    if undated:
        out.append({"code": "UNDATED_DOCS", "severity": "blocking", "count": len(undated), "record_ids": sorted(undated),
                    "message": f"{len(undated)} document(s) have no date, so FinV cannot tell whether they belong to "
                               "this period. Add the dates."})
    return out


def _sum(rows: list[DocRow], sign: int = 1) -> dict:
    t = {h: ZERO for h in HEADS}
    for r in rows:
        for h, v in (("taxable", r.taxable), ("igst", r.igst), ("cgst", r.cgst), ("sgst", r.sgst), ("cess", r.cess)):
            t[h] += sign * nz(v)
    return t


def _add(a: dict, b: dict) -> dict:
    return {h: a[h] + b[h] for h in HEADS}


def _fmt_totals(t: dict) -> dict:
    return {h: _s(v) for h, v in t.items()}


def _item(r: DocRow, rate: Decimal) -> dict:
    return {"num": 1, "itm_det": {"txval": _amt(nz(r.taxable)), "rt": float(rate), "iamt": _amt(nz(r.igst)),
                                  "camt": _amt(nz(r.cgst)), "samt": _amt(nz(r.sgst)), "csamt": _amt(nz(r.cess))}}


def _dt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def build_gstr1(gstin: str, period: str, rows: list[DocRow], undated: list[int]) -> ReturnResult:
    warnings = _common_warnings(rows, undated, {"sales"})
    sales = [r for r in rows if r.included and r.side == "sales"]
    by_id = {r.record_id: r for r in rows}
    no_rate = [r for r in sales if _rate(r) is None and (nz(r.cgst) + nz(r.sgst) + nz(r.igst)) != ZERO]
    if no_rate:
        warnings.append(_warn("RATE_UNKNOWN", "blocking",
                              "The GST rate of these documents is not a single standard slab (they may have several "
                              "rates). GSTR-1 needs the rate, so they must be split or corrected.", no_rate))
    no_pos = [r for r in sales if not (r.place_of_supply or (r.party_gstin or "")[:2])]
    if no_pos:
        warnings.append(_warn("POS_MISSING", "blocking", "Place of supply is missing on these documents.", no_pos))
    bad_gstin = [r for r in sales if r.party_gstin and not validate_gstin(r.party_gstin)[0]]
    if bad_gstin:
        warnings.append(_warn("CUSTOMER_GSTIN_INVALID", "blocking",
                              "The customer GSTIN on these documents is not valid, so they cannot go in B2B.", bad_gstin))
    skip = {r.record_id for r in no_rate + no_pos + bad_gstin}
    home = gstin[:2]

    b2b: dict[str, list] = defaultdict(list)
    b2cl: dict[str, list] = defaultdict(list)
    b2cs: dict[tuple, dict] = {}
    cdnr: dict[str, list] = defaultdict(list)
    cdnur: list = []
    sec = {k: {h: ZERO for h in HEADS} | {"count": 0} for k in ("b2b", "b2cl", "b2cs", "cdnr", "cdnur")}

    def tally(name, r, sign=1):
        for h, v in _sum([r], sign).items():
            sec[name][h] += v
        sec[name]["count"] += 1

    def b2cs_add(r, rate, sign):
        pos = r.place_of_supply
        typ = "INTRA" if pos == home else "INTER"
        k = (pos, float(rate), typ)
        e = b2cs.setdefault(k, {"sply_ty": typ, "pos": pos, "typ": "OE", "rt": float(rate),
                                "txval": ZERO, "iamt": ZERO, "camt": ZERO, "samt": ZERO, "csamt": ZERO})
        for f, v in (("txval", r.taxable), ("iamt", r.igst), ("camt", r.cgst), ("samt", r.sgst), ("csamt", r.cess)):
            e[f] += sign * nz(v)
        tally("b2cs", r, sign)

    for r in sorted(sales, key=lambda x: (x.doc_date or date.min, x.doc_number or "")):
        if r.record_id in skip:
            continue
        rate = _rate(r) or Decimal("0")
        pos = r.place_of_supply or (r.party_gstin or "")[:2]
        if r.record_type == "sales_invoice":
            inv = {"inum": r.doc_number, "idt": _dt(r.doc_date), "val": _amt(nz(r.total)), "pos": pos,
                   "itms": [_item(r, rate)]}
            if r.party_gstin:
                b2b[r.party_gstin].append(inv | {"rchrg": "N", "inv_typ": "R"})
                tally("b2b", r)
            elif pos != home and nz(r.total) > B2CL_LIMIT:
                b2cl[pos].append({k: v for k, v in inv.items() if k != "pos"})
                tally("b2cl", r)
            else:
                b2cs_add(r, rate, 1)
        else:   # sales credit / debit note
            ntty = "C" if r.record_type == "credit_note" else "D"
            sign = -1 if ntty == "C" else 1
            note = {"ntty": ntty, "nt_num": r.doc_number, "nt_dt": _dt(r.doc_date), "val": _amt(nz(r.total)),
                    "pos": pos, "itms": [_item(r, rate)]}
            if r.party_gstin:
                cdnr[r.party_gstin].append(note | {"rchrg": "N", "inv_typ": "R"})
                tally("cdnr", r, sign)
            else:
                orig = by_id.get(r.original_record_id) if r.original_record_id else None
                if orig and not orig.party_gstin and orig.place_of_supply != home and nz(orig.total) > B2CL_LIMIT:
                    cdnur.append(note | {"typ": "B2CL"})
                    tally("cdnur", r, sign)
                else:   # notes on small B2C sales are netted into B2CS, as the portal expects
                    b2cs_add(r, rate, sign)

    if sales:
        warnings.append(_warn("HSN_MISSING", "blocking",
                              "GSTR-1 Table 12 (HSN summary) is required, but FinV does not hold HSN codes. Enter the "
                              "HSN summary on the portal and confirm it with an override note."))
    warnings.append(_warn("NOT_COVERED", "info",
                          "Not prepared by FinV: exports, SEZ supplies, nil-rated detail (Table 8), advances (11A/11B) "
                          "and documents issued (Table 13). Add them on the portal if they apply."))

    payload = {"gstin": gstin, "fp": portal_period(period), "version": "GST3.2.2", "hash": "hash",
               "b2b": [{"ctin": ctin, "inv": invs} for ctin, invs in sorted(b2b.items())],
               "b2cl": [{"pos": pos, "inv": invs} for pos, invs in sorted(b2cl.items())],
               "b2cs": [{k: (_amt(v) if isinstance(v, Decimal) else v) for k, v in e.items()}
                        for _, e in sorted(b2cs.items())],
               "cdnr": [{"ctin": ctin, "nt": nts} for ctin, nts in sorted(cdnr.items())],
               "cdnur": cdnur}
    payload = {k: v for k, v in payload.items() if v != []}
    total = {h: ZERO for h in HEADS}
    for s in sec.values():
        total = {h: total[h] + s[h] for h in HEADS}
    breakdown = {k: _fmt_totals({h: v[h] for h in HEADS}) | {"count": v["count"]} for k, v in sec.items()}
    used = [r.record_id for r in sales if r.record_id not in skip]
    return ReturnResult(totals=_fmt_totals(total) | {"documents": len(used)}, breakdown=breakdown,
                        warnings=warnings, payload=payload, record_ids=sorted(used))


def build_gstr3b(gstin: str, period: str, rows: list[DocRow], undated: list[int]) -> ReturnResult:
    warnings = _common_warnings(rows, undated, {"sales", "purchase"})
    sales = [r for r in rows if r.included and r.side == "sales"]
    purchases = [r for r in rows if r.included and r.side == "purchase"]
    home = gstin[:2]

    def signed(r):
        return -1 if r.record_type == "credit_note" else 1

    taxed = [r for r in sales if (nz(r.cgst) + nz(r.sgst) + nz(r.igst) + nz(r.cess)) != ZERO]
    nil = [r for r in sales if r not in taxed]
    out_a = {h: ZERO for h in HEADS}
    for r in taxed:
        out_a = _add(out_a, _sum([r], signed(r)))
    out_c = {h: ZERO for h in HEADS}
    for r in nil:
        out_c = _add(out_c, _sum([r], signed(r)))

    inter_b2c: dict[str, dict] = defaultdict(lambda: {"taxable": ZERO, "igst": ZERO})
    for r in taxed:
        pos = r.place_of_supply
        if not r.party_gstin and pos and pos != home:
            inter_b2c[pos]["taxable"] += signed(r) * nz(r.taxable)
            inter_b2c[pos]["igst"] += signed(r) * nz(r.igst)

    eligible = [r for r in purchases if r.party_gstin and validate_gstin(r.party_gstin)[0]]
    no_itc = [r for r in purchases if r not in eligible and (nz(r.cgst) + nz(r.sgst) + nz(r.igst)) != ZERO]
    itc = {h: ZERO for h in HEADS}
    for r in eligible:
        itc = _add(itc, _sum([r], signed(r)))
    itc["taxable"] = ZERO
    if no_itc:
        warnings.append(_warn("ITC_EXCLUDED", "info",
                              "Input tax on these purchases is left out of ITC because the supplier GSTIN is missing "
                              "or invalid.", no_itc))
    if eligible:
        warnings.append(_warn("ITC_NOT_MATCHED_2B", "blocking",
                              "ITC has not been matched against GSTR-2B in FinV. Check it against 2B on the portal and "
                              "confirm with an override note before preparation."))
    warnings.append(_warn("NOT_COVERED", "info",
                          "FinV fills 3.1(a), 3.1(c), 3.2 and 4(A)(5) only. Reverse charge (3.1(d)), ineligible ITC "
                          "(4(B)), interest and late fee are left untouched on the portal."))

    payable = {h: q(out_a[h] - itc[h]) for h in ("igst", "cgst", "sgst", "cess")}
    payload = {"gstin": gstin, "ret_period": portal_period(period),
               "sup_details": {"osup_det": {"txval": _amt(out_a["taxable"]), "iamt": _amt(out_a["igst"]),
                                            "camt": _amt(out_a["cgst"]), "samt": _amt(out_a["sgst"]),
                                            "csamt": _amt(out_a["cess"])},
                               "osup_nil_exmp": {"txval": _amt(out_c["taxable"])}},
               "inter_sup": {"unreg_details": [{"pos": pos, "txval": _amt(v["taxable"]), "iamt": _amt(v["igst"])}
                                               for pos, v in sorted(inter_b2c.items())]},
               "itc_elg": {"itc_avl": [{"ty": "OTH", "iamt": _amt(itc["igst"]), "camt": _amt(itc["cgst"]),
                                        "samt": _amt(itc["sgst"]), "csamt": _amt(itc["cess"])}]}}
    totals = {"outward_taxable": _s(out_a["taxable"]), "output_igst": _s(out_a["igst"]),
              "output_cgst": _s(out_a["cgst"]), "output_sgst": _s(out_a["sgst"]), "output_cess": _s(out_a["cess"]),
              "nil_exempt": _s(out_c["taxable"]), "itc_igst": _s(itc["igst"]), "itc_cgst": _s(itc["cgst"]),
              "itc_sgst": _s(itc["sgst"]), "itc_cess": _s(itc["cess"]),
              "documents": len(sales) + len(purchases)}
    breakdown = {"3.1(a)": _fmt_totals(out_a), "3.1(c)": {"taxable": _s(out_c["taxable"])},
                 "3.2": {pos: {"taxable": _s(v["taxable"]), "igst": _s(v["igst"])} for pos, v in sorted(inter_b2c.items())},
                 "4(A)(5)": _fmt_totals({h: itc[h] for h in ("igst", "cgst", "sgst", "cess")} | {"taxable": ZERO}),
                 "payable_before_setoff": {h: _s(v) for h, v in payable.items()}}
    return ReturnResult(totals=totals, breakdown=breakdown, warnings=warnings, payload=payload,
                        record_ids=sorted(r.record_id for r in sales + purchases))


def build(return_type: str, gstin: str, period: str, rows: list[DocRow], undated: list[int]) -> ReturnResult:
    if return_type == "GSTR1":
        return build_gstr1(gstin, period, rows, undated)
    if return_type == "GSTR3B":
        return build_gstr3b(gstin, period, rows, undated)
    raise ValueError(f"Unknown return type {return_type}")


def tax_total(return_type: str, totals: dict) -> Decimal:
    if return_type == "GSTR1":
        return sum((Decimal(totals[h]) for h in ("igst", "cgst", "sgst", "cess")), ZERO)
    return sum((Decimal(totals[k]) for k in ("output_igst", "output_cgst", "output_sgst", "output_cess")), ZERO)
