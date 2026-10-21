"""Tally voucher XML import into the audit ledger."""
from tests.conftest import upload

VOUCHERS = """<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>
<TALLYMESSAGE><VOUCHER VCHTYPE="Sales">
 <DATE>20250615</DATE><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><VOUCHERNUMBER>S-1</VOUCHERNUMBER>
 <PARTYLEDGERNAME>Acme Ltd</PARTYLEDGERNAME><NARRATION>June sale</NARRATION>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Acme Ltd</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1180.00</AMOUNT></ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>1000.00</AMOUNT></ALLLEDGERENTRIES.LIST>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Output GST</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>180.00</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER></TALLYMESSAGE>
<TALLYMESSAGE><VOUCHER VCHTYPE="Payment"><ISCANCELLED>Yes</ISCANCELLED><DATE>20250616</DATE><VOUCHERNUMBER>P-1</VOUCHERNUMBER>
 <ALLLEDGERENTRIES.LIST><LEDGERNAME>Rent</LEDGERNAME><AMOUNT>-500.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE>
</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""


def test_vouchers_become_ledger_lines_with_debits_and_credits(owner):
    c, cid = owner["client"], owner["cid"]
    r = upload(c, cid, "daybook.xml", VOUCHERS, kind="auto", workspace="audit")
    assert r.status_code in (200, 201), r.text
    doc = r.json()
    assert doc["status"] == "processed" and doc["kind"] == "ledger" and doc["record_count"] == 3
    assert any("1 cancelled" in n for n in doc["notes"])
    rows = c.get(f"/api/companies/{cid}/audit/ledger").json()
    items = rows["items"] if isinstance(rows, dict) else rows
    by = {i["account_name"]: i for i in items}
    assert by["Acme Ltd"]["debit"] in ("1180.00", "1180") and by["Sales"]["credit"] in ("1000.00", "1000")
    assert {i["voucher_no"] for i in items} == {"S-1"}          # the cancelled voucher was left out


def test_utf16_exports_are_read():
    from app.ingestion.tally_import import parse_tally_vouchers
    lines, errs, _ = parse_tally_vouchers(VOUCHERS.encode("utf-16"))
    assert len(lines) == 3 and not errs


def test_entity_declarations_are_refused(owner):
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><ENVELOPE><VOUCHER>&a;</VOUCHER></ENVELOPE>'
    r = upload(owner["client"], owner["cid"], "evil.xml", evil, kind="auto", workspace="audit")
    assert r.json()["status"] == "failed" and "entities" in r.json()["error"]


def test_xml_cannot_be_a_bank_statement(owner):
    r = upload(owner["client"], owner["cid"], "d.xml", VOUCHERS, kind="bank_statement")
    assert r.json()["status"] == "failed"


def test_importing_an_overlapping_export_does_not_double_the_ledger(owner):
    c, cid = owner["client"], owner["cid"]
    first = upload(c, cid, "a.xml", VOUCHERS, kind="auto", workspace="audit").json()
    assert first["record_count"] == 3
    wider = VOUCHERS.replace("</REQUESTDATA>", """<TALLYMESSAGE><VOUCHER VCHTYPE="Sales"><DATE>20250620</DATE><VOUCHERNUMBER>S-2</VOUCHERNUMBER>
      <ALLLEDGERENTRIES.LIST><LEDGERNAME>Acme Ltd</LEDGERNAME><AMOUNT>-100.00</AMOUNT></ALLLEDGERENTRIES.LIST>
      <ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>100.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></TALLYMESSAGE></REQUESTDATA>""")
    second = upload(c, cid, "b.xml", wider, kind="auto", workspace="audit").json()
    assert second["record_count"] == 2 and any("already imported" in n for n in second["notes"])
    again = upload(c, cid, "c.xml", wider.replace("S-2", "S-2 "), kind="auto", workspace="audit").json()     # same content, nothing new
    assert again["status"] == "processed"
    total = c.get(f"/api/companies/{cid}/audit/ledger?page_size=100").json()["total"]
    assert total == 5
