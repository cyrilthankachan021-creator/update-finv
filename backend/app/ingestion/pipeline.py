"""Ingestion pipeline: file bytes -> structured records with provenance.

Supported (and only these are advertised in the UI):
  .csv / .xlsx  bank statements, invoice/note registers, trial balances, ledgers
  .pdf          invoices / credit notes / debit notes (embedded text; OCR for scans if available)
  .jpg / .png   invoices / credit notes / debit notes via OCR (tesseract, local)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..engine.money import AmountError, parse_amount
from ..engine.normalize import norm_doc_number
from .tally_import import TallyError, parse_tally_vouchers
from .tabular import DateError, TableError, cell_text, map_headers, parse_date, read_table
from .text_extract import ExtractionError, extract_invoice_fields, ocr_image_bytes, pdf_text

TABLE_EXT = {".csv", ".xlsx"}
DOC_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_EXT = TABLE_EXT | DOC_EXT | {".xml"}      # .xml: Tally voucher export
DOC_TYPES = {"purchase_invoice", "sales_invoice", "credit_note", "debit_note"}
KINDS = {"auto", "bank_statement", "invoice_register", "trial_balance", "ledger"} | DOC_TYPES
MAGIC = {".pdf": [b"%PDF-"], ".png": [b"\x89PNG\r\n\x1a\n"], ".jpg": [b"\xff\xd8\xff"], ".jpeg": [b"\xff\xd8\xff"],
         ".xlsx": [b"PK\x03\x04"]}


class IngestError(ValueError):
    pass


@dataclass
class Extraction:
    kind: str
    records: list[dict] = field(default_factory=list)
    tb_lines: list[dict] = field(default_factory=list)
    ledger_lines: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    row_errors: list[dict] = field(default_factory=list)


def ext_of(filename: str) -> str:
    i = filename.rfind(".")
    return filename[i:].lower() if i >= 0 else ""


def check_file(data: bytes, filename: str, max_bytes: int) -> str:
    ext = ext_of(filename)
    if ext not in ALLOWED_EXT:
        raise IngestError("This file type is not supported. Upload CSV, XLSX, Tally XML, PDF, JPG or PNG.")
    if len(data) == 0:
        raise IngestError("The file is empty.")
    if len(data) > max_bytes:
        raise IngestError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    if ext in MAGIC and not any(data.startswith(m) for m in MAGIC[ext]):
        raise IngestError("The file content does not match its extension.")
    if ext == ".csv" and b"\x00" in data[:4096]:
        raise IngestError("The CSV file appears to be binary, not text.")
    return ext


def classify_type(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower()
    if "credit" in t:
        return "credit_note"
    if "debit" in t:
        return "debit_note"
    if "sale" in t or "outward" in t:
        return "sales_invoice"
    if "purchase" in t or "inward" in t or t.strip() == "bill":
        return "purchase_invoice"
    return None


def classify_side(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower()
    if "sale" in t or "issued" in t or "customer" in t:
        return "sales"
    if "purchase" in t or "received" in t or "supplier" in t:
        return "purchase"
    return None


def detect_table_kind(canon: set[str]) -> str | None:
    if {"account_name", "account_code"} & canon and not {"doc_number"} & canon:
        if {"debit", "credit", "closing", "closing_dr", "opening"} & canon:
            return "ledger" if ({"date", "voucher_no"} & canon) else "trial_balance"
    if "doc_number" in canon and ({"total", "taxable"} & canon):
        return "invoice_register"
    if "date" in canon and ({"debit", "credit", "amount"} & canon) and ({"description", "reference", "balance", "closing"} & canon):
        return "bank_statement"
    return None


def extract(data: bytes, filename: str, kind: str, own_gstin: str | None = None) -> Extraction:
    if kind not in KINDS:
        raise IngestError("Unknown document type.")
    ext = ext_of(filename)
    if ext == ".xml":
        if kind not in {"auto", "ledger"}:
            raise IngestError("A Tally XML file holds vouchers. Choose document type Ledger (or Auto-detect).")
        try:
            lines, errs, notes = parse_tally_vouchers(data)
        except TallyError as exc:
            raise IngestError(str(exc)) from exc
        if not lines:
            raise IngestError("No usable ledger lines were found in the Tally file."
                              + (f" First problem: {errs[0]['message']}" if errs else ""))
        return Extraction(kind="ledger", ledger_lines=lines, notes=["Source: Tally voucher export."] + notes, row_errors=errs)
    if ext in TABLE_EXT:
        return _extract_table(data, filename, kind, ext)
    if kind not in DOC_TYPES:
        raise IngestError("For PDF and image files, choose the document type: purchase invoice, sales invoice, "
                          "credit note or debit note. Bank statements, trial balances and ledgers must be CSV or XLSX.")
    try:
        if ext == ".pdf":
            text, method = pdf_text(data)
        else:
            text, method = ocr_image_bytes(data, ext), "ocr"
    except ExtractionError as exc:
        raise IngestError(str(exc)) from exc
    fields = extract_invoice_fields(text, own_gstin, kind, method)
    notes = fields.pop("_notes", [])
    fields["doc_number_norm"] = norm_doc_number(fields.get("doc_number"))
    fields["raw"] = {"text_excerpt": text[:4000], "method": method}
    missing = [f for f in ("doc_number", "doc_date", "total", "party_name") if not fields.get(f)]
    if missing:
        notes.append("Could not read: " + ", ".join(m.replace("_", " ") for m in missing) + ". Enter them from the document.")
    return Extraction(kind=fields["record_type"], records=[fields], notes=notes)


def _amt(row_errors, rowno, col, v, allow_negative=True):
    try:
        return parse_amount(v, allow_negative=allow_negative)
    except AmountError:
        row_errors.append({"row": rowno, "message": f"'{v}' in column {col} is not an amount."})
        raise


def _date(row_errors, rowno, col, v):
    try:
        return parse_date(v)
    except DateError:
        row_errors.append({"row": rowno, "message": f"'{v}' in column {col} is not a recognised date (use DD/MM/YYYY)."})
        raise


def _extract_table(data: bytes, filename: str, kind: str, ext: str) -> Extraction:
    try:
        headers, rows = read_table(data, filename)
    except TableError as exc:
        raise IngestError(str(exc)) from exc
    mapping = map_headers(headers)
    canon = set(mapping.values())
    detected = detect_table_kind(canon)
    if kind == "auto":
        if not detected:
            raise IngestError("FinV could not tell what kind of file this is from its columns. Choose the document type and try again.")
        kind = detected
    if kind in DOC_TYPES:
        kind_for_rows, kind = kind, "invoice_register"
    else:
        kind_for_rows = None
    src = ext.lstrip(".")
    out = Extraction(kind=kind)
    out.notes.append("Columns recognised: " + ", ".join(f"{headers[i]} → {c}" for i, c in sorted(mapping.items())))
    unrecognised = [str(h) for i, h in enumerate(headers) if i not in mapping and h not in (None, "")]
    if unrecognised:
        out.notes.append("Columns ignored: " + ", ".join(unrecognised))

    for n, row in enumerate(rows, start=1):
        cells = {c: (row[i] if i < len(row) else None) for i, c in mapping.items()}
        col = {c: str(headers[i]) for i, c in mapping.items()}
        prov = {c: {"source": src, "column": col[c], "row": n, "confidence": 1.0}
                for c, v in cells.items() if v not in (None, "")}
        raw = {str(headers[i]): cell_text(row[i]) if i < len(row) else None for i in range(len(headers)) if headers[i] not in (None, "")}
        try:
            if kind == "bank_statement":
                rec = _bank_row(cells, col, n, out.row_errors)
            elif kind == "invoice_register":
                rec = _invoice_row(cells, col, n, out.row_errors, kind_for_rows)
            elif kind == "trial_balance":
                out.tb_lines.append(_tb_row(cells, col, n, out.row_errors))
                continue
            elif kind == "ledger":
                out.ledger_lines.append(_ledger_row(cells, col, n, out.row_errors))
                continue
            else:
                raise IngestError("Unsupported document type for spreadsheets.")
        except (AmountError, DateError):
            continue
        if rec is None:
            continue
        rec["row_index"] = n
        rec["provenance"] = prov
        rec["raw"] = raw
        out.records.append(rec)
    if not (out.records or out.tb_lines or out.ledger_lines):
        raise IngestError("No usable rows were found in the file." +
                          (f" First problem: row {out.row_errors[0]['row']}: {out.row_errors[0]['message']}" if out.row_errors else ""))
    return out


def _bank_row(c, col, n, errs) -> dict | None:
    d = _date(errs, n, col.get("date"), c.get("date"))
    debit = _amt(errs, n, col.get("debit"), c.get("debit"), False) if "debit" in c else None
    credit = _amt(errs, n, col.get("credit"), c.get("credit"), False) if "credit" in c else None
    if "amount" in c and debit is None and credit is None:
        a = _amt(errs, n, col.get("amount"), c.get("amount"))
        if a is not None:
            debit, credit = (-a, None) if a < 0 else (None, a)
    debit = debit or None
    credit = credit or None
    if debit is None and credit is None:
        return None  # balance-only, opening/closing or summary row: no transaction amount
    if d is None:
        errs.append({"row": n, "message": "Bank line has no date."})
        return None
    return {"record_type": "bank_txn", "doc_date": d, "description": cell_text(c.get("description")),
            "reference": cell_text(c.get("reference")), "debit_amount": debit, "credit_amount": credit,
            "balance": (_amt(errs, n, col.get("balance"), c.get("balance")) if "balance" in c else
                        _amt(errs, n, col.get("closing"), c.get("closing")) if "closing" in c else None)}


def _invoice_row(c, col, n, errs, forced_type) -> dict | None:
    rtype = forced_type or classify_type(cell_text(c.get("doc_type")))
    if forced_type and classify_type(cell_text(c.get("doc_type"))) in {"credit_note", "debit_note"}:
        rtype = classify_type(cell_text(c.get("doc_type")))
    if rtype is None:
        errs.append({"row": n, "message": "Document type is missing or not recognised (use Purchase, Sales, Credit Note or Debit Note)."})
        return None
    rec = {"record_type": rtype,
           "party_name": cell_text(c.get("party")),
           "party_gstin": (cell_text(c.get("gstin")) or "").upper() or None,
           "doc_number": cell_text(c.get("doc_number")),
           "original_doc_number": cell_text(c.get("original_doc_number")),
           "note_side": classify_side(cell_text(c.get("side"))),
           "place_of_supply": (cell_text(c.get("place_of_supply")) or "")[:2].zfill(2) if cell_text(c.get("place_of_supply")) else None,
           "category_hint": cell_text(c.get("category")),
           "doc_date": _date(errs, n, col.get("date"), c.get("date")),
           "due_date": _date(errs, n, col.get("due_date"), c.get("due_date")) if "due_date" in c else None}
    for f in ("taxable", "cgst", "sgst", "igst", "cess", "total"):
        rec[f] = _amt(errs, n, col.get(f), c.get(f)) if f in c else None
    if not rec["doc_number"] and rec["total"] is None:
        return None
    rec["doc_number_norm"] = norm_doc_number(rec["doc_number"])
    return rec


def _signed(errs, n, col, c, single, dr, cr) -> Decimal | None:
    if single in c and c.get(single) not in (None, ""):
        return _amt(errs, n, col.get(single), c.get(single))
    d = _amt(errs, n, col.get(dr), c.get(dr)) if dr in c else None
    k = _amt(errs, n, col.get(cr), c.get(cr)) if cr in c else None
    if d is None and k is None:
        return None
    return (d or Decimal("0")) - (k or Decimal("0"))


def _tb_row(c, col, n, errs) -> dict:
    name = cell_text(c.get("account_name")) or cell_text(c.get("description"))
    return {"row_index": n, "account_code": cell_text(c.get("account_code")), "account_name": name,
            "account_type": _norm_type(cell_text(c.get("account_type")) or cell_text(c.get("doc_type"))),
            "opening": _signed(errs, n, col, c, "opening", "opening_dr", "opening_cr"),
            "debit": _amt(errs, n, col.get("debit"), c.get("debit")) or Decimal("0") if "debit" in c else Decimal("0"),
            "credit": _amt(errs, n, col.get("credit"), c.get("credit")) or Decimal("0") if "credit" in c else Decimal("0"),
            "closing": _signed(errs, n, col, c, "closing", "closing_dr", "closing_cr")}


def _ledger_row(c, col, n, errs) -> dict:
    return {"row_index": n, "entry_date": _date(errs, n, col.get("date"), c.get("date")),
            "account_code": cell_text(c.get("account_code")),
            "account_name": cell_text(c.get("account_name")),
            "voucher_no": cell_text(c.get("voucher_no")) or cell_text(c.get("doc_number")),
            "voucher_type": cell_text(c.get("voucher_type")) or cell_text(c.get("doc_type")),
            "party": cell_text(c.get("party")), "reference": cell_text(c.get("reference")),
            "narration": cell_text(c.get("description")),
            "debit": _amt(errs, n, col.get("debit"), c.get("debit")) or Decimal("0") if "debit" in c else Decimal("0"),
            "credit": _amt(errs, n, col.get("credit"), c.get("credit")) or Decimal("0") if "credit" in c else Decimal("0")}


def _norm_type(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower()
    for key, typ in [("asset", "asset"), ("liabil", "liability"), ("equity", "equity"), ("capital", "equity"),
                     ("income", "income"), ("revenue", "income"), ("expense", "expense")]:
        if key in t:
            return typ
    return None
