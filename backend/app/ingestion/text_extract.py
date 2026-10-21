"""Text extraction from PDFs and images, and pattern-based invoice field extraction.

Every extracted field records its source and a confidence. Fields that cannot be found are left
empty — never guessed — and the record is sent for review.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import settings
from ..engine.gst import GSTIN_RE
from ..engine.money import AmountError, parse_amount
from .tabular import DateError, parse_date

OCR_TIMEOUT_S = 60
MAX_PDF_OCR_PAGES = 5


class ExtractionError(RuntimeError):
    pass


def ocr_available() -> bool:
    return shutil.which(settings.tesseract_cmd) is not None


def ocr_image_bytes(data: bytes, suffix: str) -> str:
    if not ocr_available():
        raise ExtractionError("Image text recognition (OCR) is not installed on this machine.")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"img{suffix}"
        p.write_bytes(data)
        try:
            proc = subprocess.run([settings.tesseract_cmd, str(p), "stdout"], capture_output=True,
                                  timeout=OCR_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("Text recognition took too long for this image.") from exc
        if proc.returncode != 0:
            raise ExtractionError("Text recognition failed for this image.")
        return proc.stdout.decode("utf-8", "replace")


def pdf_text(data: bytes) -> tuple[str, str]:
    """Return (text, method). Uses embedded text; falls back to OCR of rendered pages if available."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("The PDF is password-protected. Remove the password and upload again.")
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:50])
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("The PDF could not be read.") from exc
    if len(text.strip()) >= 40:
        return text, "pdf_text"
    if shutil.which("pdftoppm") and ocr_available():
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.pdf"
            src.write_bytes(data)
            try:
                subprocess.run(["pdftoppm", "-r", "200", "-png", "-l", str(MAX_PDF_OCR_PAGES), str(src),
                                str(Path(td) / "pg")], capture_output=True, timeout=OCR_TIMEOUT_S, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                raise ExtractionError("The scanned PDF could not be rendered for text recognition.") from exc
            parts = [ocr_image_bytes(p.read_bytes(), ".png") for p in sorted(Path(td).glob("pg*.png"))]
            return "\n".join(parts), "ocr"
    raise ExtractionError("The PDF has no readable text and OCR is unavailable.")


_NUM = r"\(?-?[\d,]+(?:\.\d{1,2})?\)?"
_DATE = r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4}"


def _last_amount(line: str):
    nums = re.findall(_NUM, line)
    for n in reversed(nums):
        try:
            v = parse_amount(n)
        except AmountError:
            continue
        if v is not None and ("." in n or "," in n or len(n.strip("()-")) >= 2):
            return v
    return None


def extract_invoice_fields(text: str, own_gstin: str | None, default_type: str, method: str) -> dict:
    conf = 0.6 if method == "pdf_text" else 0.4
    rec: dict = {}
    prov: dict = {}

    def put(field, value, how):
        if value is not None and value != "":
            rec[field] = value
            prov[field] = {"source": method, "method": how, "confidence": conf}

    low = text.lower()
    rtype = default_type
    if re.search(r"\bcredit\s+note\b", low):
        rtype = "credit_note"
    elif re.search(r"\bdebit\s+note\b", low):
        rtype = "debit_note"
    rec["record_type"] = rtype
    prov["record_type"] = {"source": method, "method": "keyword" if rtype != default_type else "upload choice",
                           "confidence": conf if rtype != default_type else 1.0}

    num_label = r"(?:credit\s*note|debit\s*note|note)" if rtype in {"credit_note", "debit_note"} else r"(?:invoice|inv|bill)"
    m = re.search(num_label + r"\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})", text, re.IGNORECASE)
    put("doc_number", m.group(1).strip() if m else None, "labelled number")

    m = re.search(r"(?:invoice|note|bill)?\s*date\s*[:\-]?\s*(" + _DATE + ")", text, re.IGNORECASE)
    if m:
        try:
            put("doc_date", parse_date(m.group(1)), "labelled date")
        except DateError:
            pass

    gstins = [g for g in re.findall(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", text.upper())
              if GSTIN_RE.match(g)]
    others = [g for g in dict.fromkeys(gstins) if not own_gstin or g != own_gstin.upper()]
    if len(others) == 1:
        put("party_gstin", others[0], "only non-company GSTIN on the document")
    elif len(others) > 1:
        rec.setdefault("_notes", []).append("Several GSTINs found; the party GSTIN needs confirmation.")

    party_pat = (r"(?:supplier|vendor|seller|billed\s*by|from)\s*(?:name)?\s*[:\-]\s*(.+)" if rtype != "sales_invoice"
                 else r"(?:buyer|customer|bill\s*to|billed\s*to)\s*(?:name)?\s*[:\-]\s*(.+)")
    m = re.search(party_pat, text, re.IGNORECASE)
    put("party_name", m.group(1).strip()[:200] if m else None, "labelled party")

    m = re.search(r"(?:against|original|ref(?:erence)?\.?)\s*(?:to\s*)?(?:invoice|inv)\s*(?:no|number|#)?\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})",
                  text, re.IGNORECASE)
    if rtype in {"credit_note", "debit_note"}:
        put("original_doc_number", m.group(1).strip() if m else None, "labelled original invoice")

    m = re.search(r"place\s*of\s*supply\s*[:\-]?\s*(?:.*?\()?(\d{2})\b", text, re.IGNORECASE)
    put("place_of_supply", m.group(1) if m else None, "labelled place of supply")

    for line in text.splitlines():
        l = line.lower()
        if "taxable" in l and "taxable" not in rec:
            put("taxable", _last_amount(line), "line labelled taxable")
        for head in ("cgst", "sgst", "igst", "cess"):
            if re.search(r"\b" + head + r"\b", l) and head not in rec:
                put(head, _last_amount(line), f"line labelled {head.upper()}")
        if re.search(r"\b(grand\s*total|invoice\s*total|total\s*amount|total\s*invoice\s*value|amount\s*payable|net\s*payable)\b", l):
            put("total", _last_amount(line), "line labelled total")
    if "total" not in rec:
        for line in text.splitlines():
            if re.match(r"\s*total\b", line, re.IGNORECASE):
                put("total", _last_amount(line), "line starting with 'Total'")
    rec["provenance"] = prov
    return rec
