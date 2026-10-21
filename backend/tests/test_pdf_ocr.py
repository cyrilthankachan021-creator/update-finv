"""Real PDF and image (OCR) uploads through the API, using a generated fictional invoice."""
import shutil
import subprocess

import pytest

from tests.conftest import COMPANY_GSTIN, SUPPLIER_MH, upload

LINES = [
    "TAX INVOICE",
    "Supplier: Sharma Stationers",
    f"GSTIN: {SUPPLIER_MH}",
    f"Buyer GSTIN: {COMPANY_GSTIN}",
    "Invoice No: INV-7781",
    "Invoice Date: 10/06/2025",
    "Place of Supply: Maharashtra (27)",
    "Taxable Value 10,000.00",
    "CGST 9% 900.00",
    "SGST 9% 900.00",
    "Grand Total 11,800.00",
]


def make_pdf(lines: list[str]) -> bytes:
    """Minimal single-page PDF with Helvetica text (valid xref table)."""
    text = "BT /F1 14 Tf 60 780 Td 20 TL " + " ".join(
        "(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _record(c, cid):
    items = c.get(f"/api/companies/{cid}/records").json()["items"]
    assert len(items) == 1
    return c.get(f"/api/companies/{cid}/records/{items[0]['id']}").json()


def test_pdf_invoice_upload_extracts_and_validates(owner):
    c, cid = owner["client"], owner["cid"]
    r = upload(c, cid, "invoice.pdf", make_pdf(LINES), kind="purchase_invoice")
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "processed", r.json()
    rec = _record(c, cid)
    f = rec["record"]
    assert (f["doc_number"], f["party_gstin"], f["total_amount"], f["cgst"], f["place_of_supply"], f["doc_date"]) == \
        ("INV-7781", SUPPLIER_MH, "11800.00", "900.00", "27", "2025-06-10")
    assert f["provenance"]["total"]["source"] == "pdf_text" and f["provenance"]["total"]["confidence"] < 1
    e = rec["entries"][0]
    v = {x["rule_id"]: x["status"] for x in e["validation"]}
    assert v["ACC-001"] == "pass" and v["GST-002"] == "pass" and v["ACC-007"] == "pass"
    # the PDF is retrievable for preview and served inline
    file = c.get(f"/api/companies/{cid}/documents/{f['document']['id']}/file")
    assert file.headers["content-type"] == "application/pdf" and file.content.startswith(b"%PDF")


def test_pdf_missing_fields_are_left_for_review(owner):
    c, cid = owner["client"], owner["cid"]
    r = upload(c, cid, "partial.pdf", make_pdf(["TAX INVOICE", "Supplier: Unknown Vendor", "Invoice No: X-1",
                                               "Some text without amounts or dates on this page"]), kind="purchase_invoice")
    doc = r.json()
    assert doc["status"] == "processed" and any("Could not read" in n for n in doc["notes"])
    rec = _record(c, cid)
    assert rec["record"]["total_amount"] is None  # never invented
    assert rec["entries"][0]["lines"] == [] and rec["record"]["status"] == "needs_review"


@pytest.mark.skipif(not (shutil.which("pdftoppm") and shutil.which("tesseract")), reason="pdftoppm/tesseract not installed")
def test_image_invoice_via_local_ocr(owner, tmp_path):
    c, cid = owner["client"], owner["cid"]
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(make_pdf(LINES))
    subprocess.run(["pdftoppm", "-r", "200", "-png", "-singlefile", str(pdf), str(tmp_path / "inv")], check=True)
    png = (tmp_path / "inv.png").read_bytes()
    r = upload(c, cid, "scan.png", png, kind="purchase_invoice")
    assert r.status_code == 201 and r.json()["status"] == "processed", r.text
    f = _record(c, cid)["record"]
    assert f["provenance"]["total"]["source"] == "ocr"
    assert f["total_amount"] == "11800.00" and f["doc_number"] == "INV-7781"
