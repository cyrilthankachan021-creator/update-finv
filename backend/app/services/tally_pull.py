"""Pull vouchers (the Day Book) from a running TallyPrime into the client's audit ledger.

Prerequisite: TallyPrime is open with the company loaded and its HTTP server enabled (F1 Help > Settings > Connectivity,
port 9000). FinV sends one standard "Export Data / Day Book" request and imports whatever vouchers come back through the
same path as an uploaded Tally XML file, so validation, source tracking and duplicate protection are identical. FinV has
not been run against a live Tally in this environment: failures are reported as they are, never as a success.
"""
from __future__ import annotations

from datetime import date, datetime
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from ..ingestion.tally_import import TallyError, parse_tally_vouchers
from ..models import Company, utcnow
from . import documents_service
from .audit_log import log_event
from .audit_service import AuditWorkflowError as WorkflowError
from .tally_service import check_tally_url

MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def build_request(tally_company: str, start: date, end: date) -> str:
    return ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID></HEADER>"
            "<BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            f"<SVCURRENTCOMPANY>{escape(tally_company)}</SVCURRENTCOMPANY>"
            f"<SVFROMDATE>{start:%Y%m%d}</SVFROMDATE><SVTODATE>{end:%Y%m%d}</SVTODATE></STATICVARIABLES></DESC></BODY></ENVELOPE>")


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def pull_daybook(db: Session, company: Company, user_id: int, url: str, tally_company: str, start: date, end: date) -> dict:
    import httpx
    if end < start:
        raise WorkflowError("The end date is before the start date.", 422)
    if (end - start).days > 366:
        raise WorkflowError("Choose a period of at most one year at a time.", 422)
    url = check_tally_url(url)
    try:
        r = httpx.post(url, content=build_request(tally_company, start, end), headers={"Content-Type": "text/xml"}, timeout=90)
    except httpx.HTTPError:
        log_event(db, company.id, user_id, "tally.pull_failed", None, None, {"reason": "no answer", "url": url})
        raise WorkflowError(f"Tally did not answer at {url}. Open TallyPrime with this company loaded and enable its HTTP "
                            "server (port 9000), or export the Day Book as XML in Tally and upload the file instead.", 503)
    data = r.content
    if r.status_code != 200 or len(data) > MAX_RESPONSE_BYTES:
        log_event(db, company.id, user_id, "tally.pull_failed", None, None, {"status": r.status_code})
        raise WorkflowError(f"Tally answered with an error (HTTP {r.status_code}). Nothing was imported.", 502)
    try:
        lines, errs, notes = parse_tally_vouchers(data)
    except TallyError as exc:
        text = data[:2000].decode("utf-8", "ignore")
        detail = " Tally said: " + text.split("<LINEERROR>")[1].split("</LINEERROR>")[0].strip() if "<LINEERROR>" in text else ""
        log_event(db, company.id, user_id, "tally.pull_failed", None, None, {"reason": str(exc)})
        raise WorkflowError(f"{exc}{detail} Nothing was imported.", 422)
    doc, _ = documents_service.upload(db, company, user_id, f"tally-daybook-{start:%Y%m%d}-{end:%Y%m%d}.xml", data, "ledger", "audit",
                                      None, None, f"Tally Day Book {start} to {end}", start, end)
    dates = sorted({l["entry_date"] for l in lines if l["entry_date"]})
    covered = {f"{d.year}-{d.month:02d}" for d in dates}
    missing = [m for m in _months(start, end) if m not in covered]
    out = {"ok": True, "document_id": doc.id, "status": doc.status, "synced_at": utcnow().isoformat(), "period": [str(start), str(end)],
           "vouchers_read": len({(l["voucher_no"], l["voucher_type"], l["entry_date"]) for l in lines}), "lines_read": len(lines),
           "lines_added": doc.record_count, "rejected": len(errs), "months_without_vouchers": missing,
           "notes": list(doc.extraction_notes or [])}
    log_event(db, company.id, user_id, "tally.pull", "document", doc.id, {k: out[k] for k in ("period", "vouchers_read", "lines_added", "rejected", "months_without_vouchers")})
    return out
