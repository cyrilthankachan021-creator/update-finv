"""Tally integration: posted FinV entries -> Tally XML (ledger masters + vouchers).

Two ways to use it:
- File: download the XML and import it in Tally (Import > Masters, then Import > Vouchers).
- Push: send the XML to Tally's built-in HTTP server (TallyPrime: F1 Help > Settings > Connectivity,
  "TallyPrime acts as" Server, port 9000). Only addresses on this machine or the local network are accepted.

Each entry is exported once; re-exporting needs an explicit choice. Only *posted* entries are exported.
Party lines (Trade Payables / Receivables with a known party) use party ledgers under Sundry Creditors /
Sundry Debtors, as Tally users normally keep them.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import xml.etree.ElementTree as ET
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, JournalEntry, TallyExport
from .accounting_service import WorkflowError
from .audit_log import log_event

VOUCHER_TYPES = {
    "purchase_invoice": "Purchase", "sales_invoice": "Sales", "credit_note": "Credit Note", "debit_note": "Debit Note",
    "bank_supplier_payment": "Payment", "bank_customer_receipt": "Receipt",
}
ROLE_GROUPS = {
    "bank": "Bank Accounts", "cash": "Cash-in-Hand", "payables": "Sundry Creditors", "receivables": "Sundry Debtors",
    "purchases": "Purchase Accounts", "purchase_returns": "Purchase Accounts", "sales": "Sales Accounts",
    "sales_returns": "Sales Accounts", "round_off": "Indirect Expenses",
}
TYPE_GROUPS = {"asset": "Current Assets", "liability": "Current Liabilities", "equity": "Capital Account",
               "income": "Indirect Incomes", "expense": "Indirect Expenses"}
FIXED_ASSET_WORDS = re.compile(r"furniture|equipment|machinery|building|vehicle|computer|plant", re.I)


def _group(acct) -> str:
    role = acct.role or ""
    if role.startswith(("input_", "output_", "tds_")):
        return "Duties & Taxes"
    if role in ROLE_GROUPS:
        return ROLE_GROUPS[role]
    if acct.type == "asset" and FIXED_ASSET_WORDS.search(acct.name):
        return "Fixed Assets"
    if acct.type == "liability" and "loan" in acct.name.lower():
        return "Loans (Liability)"
    return TYPE_GROUPS.get(acct.type, "Suspense A/c")


def _ledger_for_line(line, entry) -> tuple[str, str]:
    acct = line.account
    rec = entry.source_record
    if acct.role in {"payables", "receivables"} and rec and rec.party_name:
        return rec.party_name.strip(), "Sundry Creditors" if acct.role == "payables" else "Sundry Debtors"
    return acct.name, _group(acct)


def _voucher_type(entry) -> str:
    t = entry.treatment or ""
    if t in VOUCHER_TYPES:
        return VOUCHER_TYPES[t]
    return "Journal"


def pending_entries(db: Session, company: Company, include_exported: bool = False) -> list[JournalEntry]:
    stmt = select(JournalEntry).where(JournalEntry.company_id == company.id, JournalEntry.status == "posted")
    if not include_exported:
        done = select(TallyExport.entry_id).where(TallyExport.company_id == company.id, TallyExport.status == "exported")
        stmt = stmt.where(JournalEntry.id.notin_(done))
    return db.scalars(stmt.order_by(JournalEntry.entry_date, JournalEntry.id)).all()


def _envelope(report: str, tally_company: str) -> tuple[ET.Element, ET.Element]:
    env = ET.Element("ENVELOPE")
    ET.SubElement(ET.SubElement(env, "HEADER"), "TALLYREQUEST").text = "Import Data"
    imp = ET.SubElement(ET.SubElement(env, "BODY"), "IMPORTDATA")
    desc = ET.SubElement(imp, "REQUESTDESC")
    ET.SubElement(desc, "REPORTNAME").text = report
    ET.SubElement(ET.SubElement(desc, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = tally_company
    return env, ET.SubElement(imp, "REQUESTDATA")


def _amount(v: Decimal) -> str:
    return f"{v:.2f}"


def build_xml(company: Company, entries: list[JournalEntry], tally_company: str) -> tuple[bytes, bytes, dict]:
    """Returns (masters_xml, vouchers_xml, summary)."""
    ledgers: dict[str, str] = {}
    env_v, data_v = _envelope("Vouchers", tally_company)
    for e in entries:
        if not e.lines or any(l.account is None for l in e.lines):
            raise WorkflowError(f"Entry #{e.id} has a line without a ledger and cannot be exported.")
        msg = ET.SubElement(data_v, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
        vtype = _voucher_type(e)
        v = ET.SubElement(msg, "VOUCHER", {"VCHTYPE": vtype, "ACTION": "Create",
                                           "REMOTEID": f"FINV-{company.id}-{e.id}"})
        ET.SubElement(v, "DATE").text = e.entry_date.strftime("%Y%m%d") if e.entry_date else ""
        ET.SubElement(v, "VOUCHERTYPENAME").text = vtype
        rec = e.source_record
        ET.SubElement(v, "VOUCHERNUMBER").text = (rec.doc_number if rec and rec.doc_number else f"FINV-{e.id}")[:50]
        if rec and rec.doc_number and rec.record_type != "bank_txn":
            ET.SubElement(v, "REFERENCE").text = rec.doc_number[:50]
        party = None
        for l in e.lines:
            name, group = _ledger_for_line(l, e)
            ledgers.setdefault(name, group)
            if group in {"Sundry Creditors", "Sundry Debtors"}:
                party = party or name
        if party:
            ET.SubElement(v, "PARTYLEDGERNAME").text = party
        ET.SubElement(v, "NARRATION").text = f"{e.narration or ''} [FinV entry #{e.id} v{e.version}]".strip()
        for l in e.lines:
            name, _ = _ledger_for_line(l, e)
            le = ET.SubElement(v, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(le, "LEDGERNAME").text = name
            is_debit = l.debit > 0
            # Tally convention: debit lines are "deemed positive" with a negative amount
            ET.SubElement(le, "ISDEEMEDPOSITIVE").text = "Yes" if is_debit else "No"
            ET.SubElement(le, "AMOUNT").text = _amount(-l.debit if is_debit else l.credit)
    env_m, data_m = _envelope("All Masters", tally_company)
    for name, group in sorted(ledgers.items()):
        msg = ET.SubElement(data_m, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
        led = ET.SubElement(msg, "LEDGER", {"NAME": name, "ACTION": "Create"})
        ET.SubElement(led, "NAME").text = name
        ET.SubElement(led, "PARENT").text = group
    summary = {"vouchers": len(entries), "ledgers": len(ledgers),
               "voucher_types": sorted({_voucher_type(e) for e in entries})}
    return (ET.tostring(env_m, encoding="utf-8", xml_declaration=True),
            ET.tostring(env_v, encoding="utf-8", xml_declaration=True), summary)


def check_tally_url(url: str) -> str:
    """Only http(s) to this machine or a private network address (prevents the server calling the internet)."""
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise WorkflowError("Enter Tally's address, for example http://127.0.0.1:9000", 422)
    host = u.hostname
    if host == "localhost":
        return url
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise WorkflowError("Use Tally's IP address (for example 127.0.0.1 or 192.168.x.x), not a host name.", 422)
    if not (ip.is_loopback or ip.is_private):
        raise WorkflowError("FinV only sends data to Tally on this machine or your local network.", 422)
    return url


def parse_response(text: str) -> dict:
    def num(tag):
        m = re.search(rf"<{tag}>\s*(\d+)\s*</{tag}>", text)
        return int(m.group(1)) if m else 0
    errors = re.findall(r"<LINEERROR>(.*?)</LINEERROR>", text, re.S)
    return {"created": num("CREATED"), "altered": num("ALTERED"), "errors": num("ERRORS") or len(errors),
            "exceptions": num("EXCEPTIONS"), "messages": [e.strip() for e in errors][:20]}


def record_export(db: Session, company: Company, entries: list[JournalEntry], user_id: int, method: str,
                  response: dict | None) -> str:
    batch = secrets.token_hex(6)
    for e in entries:
        existing = db.scalar(select(TallyExport).where(TallyExport.entry_id == e.id))
        if existing:
            existing.batch_id, existing.method, existing.response, existing.status = batch, method, response, "exported"
            existing.created_by = user_id
        else:
            db.add(TallyExport(company_id=company.id, entry_id=e.id, batch_id=batch, method=method, status="exported",
                               response=response, created_by=user_id))
    log_event(db, company.id, user_id, f"tally.{method}", None, None,
              {"batch": batch, "entries": [e.id for e in entries], "response": response})
    db.flush()
    return batch


def push(db: Session, company: Company, user_id: int, url: str, tally_company: str, include_exported: bool) -> dict:
    import httpx
    url = check_tally_url(url)
    entries = pending_entries(db, company, include_exported)
    if not entries:
        return {"ok": True, "message": "Nothing to send: every posted entry has already been exported to Tally.",
                "vouchers": 0}
    masters, vouchers, summary = build_xml(company, entries, tally_company)
    try:
        r_m = httpx.post(url, content=masters, headers={"Content-Type": "text/xml"}, timeout=30)
        r_v = httpx.post(url, content=vouchers, headers={"Content-Type": "text/xml"}, timeout=60)
    except httpx.HTTPError:
        raise WorkflowError(f"Tally did not answer at {url}. Open TallyPrime with the company loaded and enable "
                            "its HTTP server (port 9000), or use the file export instead.", 503)
    res_m, res_v = parse_response(r_m.text), parse_response(r_v.text)
    out = {"masters": res_m, "vouchers_result": res_v, **summary}
    if r_v.status_code != 200 or res_v["errors"] or res_v["exceptions"]:
        log_event(db, company.id, user_id, "tally.push_failed", None, None, {"response": out})
        raise WorkflowError("Tally rejected some vouchers; nothing was marked as exported. "
                            + "; ".join(res_v["messages"][:3]), 409, {"tally": out})
    if res_v["created"] + res_v["altered"] < len(entries):
        log_event(db, company.id, user_id, "tally.push_incomplete", None, None, {"response": out})
        raise WorkflowError(f"Tally accepted {res_v['created'] + res_v['altered']} of {len(entries)} vouchers. "
                            "Check the company is open in Tally; nothing was marked as exported.", 409, {"tally": out})
    out["batch"] = record_export(db, company, entries, user_id, "push", out)
    out["ok"] = True
    out["message"] = f"Tally created {res_v['created']} voucher(s) and {res_m['created']} new ledger(s)."
    return out


def mark_exported(db: Session, company: Company, user_id: int, entry_ids: list[int]) -> str:
    entries = [e for e in db.scalars(select(JournalEntry).where(JournalEntry.company_id == company.id,
                                                               JournalEntry.id.in_(entry_ids))).all()]
    if len(entries) != len(set(entry_ids)):
        raise WorkflowError("Some entries were not found.", 404)
    if any(e.status != "posted" for e in entries):
        raise WorkflowError("Only posted entries can be exported to Tally.")
    return record_export(db, company, entries, user_id, "file", None)
