"""Read CSV/XLSX into rows and map headers to canonical fields.

Spreadsheet formulas are never evaluated: openpyxl is opened with data_only=True, which returns the
value cached by the spreadsheet application (or None). Cell content is treated as untrusted text.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

SYNONYMS: dict[str, list[str]] = {
    "date": ["date", "txn date", "transaction date", "value date", "posting date", "invoice date", "doc date",
             "document date", "note date", "voucher date", "bill date", "tran date"],
    "description": ["description", "narration", "particulars", "details", "remarks", "transaction details",
                    "transaction remarks"],
    "reference": ["reference", "ref", "ref no", "reference no", "reference number", "cheque no", "chq no",
                  "chq ref no", "utr", "utr no", "transaction id", "instrument no", "chq/ref no"],
    "debit": ["debit", "withdrawal", "withdrawals", "debit amount", "dr", "withdrawal amt", "withdrawal amount",
              "paid out", "dr amount"],
    "credit": ["credit", "deposit", "deposits", "credit amount", "cr", "deposit amt", "deposit amount",
               "paid in", "cr amount"],
    "amount": ["amount", "transaction amount"],
    "balance": ["balance", "running balance", "available balance"],
    "party": ["party", "party name", "supplier", "supplier name", "vendor", "vendor name", "customer",
              "customer name", "buyer", "seller", "name of supplier", "name of customer", "counterparty"],
    "gstin": ["gstin", "gst no", "gstin/uin", "supplier gstin", "customer gstin", "party gstin", "gst number",
              "gstin of supplier", "gstin of customer"],
    "doc_number": ["invoice no", "invoice number", "inv no", "bill no", "bill number", "document no",
                   "document number", "doc no", "note no", "note number", "credit note no", "debit note no",
                   "invoice #"],
    "original_doc_number": ["original invoice no", "original invoice number", "against invoice",
                            "reference invoice", "invoice reference", "orig inv no", "original invoice"],
    "doc_type": ["type", "document type", "doc type", "record type"],
    "side": ["side", "note side"],
    "place_of_supply": ["place of supply", "pos", "place of supply code"],
    "taxable": ["taxable value", "taxable amount", "assessable value", "taxable"],
    "cgst": ["cgst", "cgst amount", "central tax"],
    "sgst": ["sgst", "sgst amount", "sgst/utgst", "utgst", "state tax"],
    "igst": ["igst", "igst amount", "integrated tax"],
    "cess": ["cess", "cess amount"],
    "total": ["total", "invoice value", "total amount", "invoice total", "grand total", "net amount",
              "invoice amount", "note value", "total value", "gross amount"],
    "due_date": ["due date", "payment due date"],
    "category": ["category", "expense head", "account head", "ledger head"],
    # trial balance / ledger
    "account_code": ["account code", "ledger code", "code", "gl code", "acc code", "a/c code"],
    "account_name": ["account name", "ledger name", "ledger", "account", "gl name", "a/c name", "account head name"],
    "account_type": ["account type", "group", "type of account", "classification", "nature"],
    "opening": ["opening", "opening balance", "op balance"],
    "opening_dr": ["opening dr", "opening debit"],
    "opening_cr": ["opening cr", "opening credit"],
    "closing": ["closing", "closing balance", "closing bal", "cl balance"],
    "closing_dr": ["closing dr", "closing debit"],
    "closing_cr": ["closing cr", "closing credit"],
    "voucher_no": ["voucher no", "voucher number", "vch no", "jv no", "entry no"],
    "voucher_type": ["voucher type", "vch type"],
}

_LOOKUP = {}
for canon, names in SYNONYMS.items():
    for n in names:
        _LOOKUP[n] = canon


def norm_header(h) -> str:
    s = str(h or "").strip().lower()
    s = re.sub(r"[\(\)\[\]₹:.]|\binr\b|\brs\b", " ", s)
    s = re.sub(r"[_\-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def map_headers(headers: list) -> dict[int, str]:
    mapping: dict[int, str] = {}
    used: set[str] = set()
    for i, h in enumerate(headers):
        canon = _LOOKUP.get(norm_header(h))
        if canon and canon not in used:
            mapping[i] = canon
            used.add(canon)
    return mapping


class TableError(ValueError):
    pass


def read_table(data: bytes, filename: str) -> tuple[list, list[list]]:
    """Return (headers, rows). The header row is the first row with >= 2 recognised headers."""
    name = filename.lower()
    if name.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect)]
    elif name.endswith(".xlsx"):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise TableError("The spreadsheet could not be opened. Save it as .xlsx or CSV and try again.") from exc
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        raise TableError("Unsupported table format.")
    # find header row within the first 30 rows (bank exports often have a preamble)
    for i, r in enumerate(rows[:30]):
        if len(map_headers(r)) >= 2:
            body = [x for x in rows[i + 1:] if any(c not in (None, "") and str(c).strip() for c in x)]
            return r, body
    raise TableError("No recognisable column headers were found (for example Date, Description, Debit, Credit, "
                     "Invoice No, Total). Check the file has a header row.")


_DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d/%m/%y",
                 "%d-%m-%y", "%d %B %Y", "%Y/%m/%d", "%d-%B-%Y", "%Y-%m-%d %H:%M:%S"]


class DateError(ValueError):
    pass


def parse_date(v) -> date | None:
    """Indian day-first convention for numeric dates (DD/MM/YYYY). Documented in ACCOUNTING_RULES.md."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise DateError(f"Unrecognised date '{s}'")


def cell_text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None
