"""End-to-end Accounting workflow through the HTTP API (Definition of Done steps 4-14)."""
from tests.conftest import SUPPLIER_KA, SUPPLIER_MH, upload

INVOICES = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,IGST,Total,Category,Original Invoice No
Purchase,INV-001,10/06/2025,Sharma Supplies,{SUPPLIER_MH},27,1000.00,90.00,90.00,,1180.00,Rent,
Purchase,INV-002,12/06/2025,Kaveri Traders,{SUPPLIER_KA},27,5000.00,,,900.00,5900.00,,
Credit Note,CN-1,15/06/2025,Sharma Supplies,{SUPPLIER_MH},27,100.00,9.00,9.00,,118.00,,INV-001
"""
# Same invoice INV-001 re-keyed in another export (different file bytes, number formatted differently)
REPEAT = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,IGST,Total
Purchase,inv 001,10/06/2025,Sharma Supplies Pvt Ltd,{SUPPLIER_MH},27,1000.00,90.00,90.00,,1180.00
Purchase,INV-001,10/06/2025,Sharma Supplies,{SUPPLIER_MH},27,1100.00,99.00,99.00,,1298.00
"""
BANK = """Date,Description,Reference,Debit,Credit,Balance
20/06/2025,NEFT/SHARMA SUPPLIES/INV-001,UTR100,1180.00,,48820.00
21/06/2025,NEFT CR FROM ABC,UTR101,,25000.00,73820.00
"""


def _records(c, cid, **params):
    return c.get(f"/api/companies/{cid}/records", params=params).json()["items"]


def test_full_accounting_workflow(owner):
    c, cid = owner["client"], owner["cid"]
    base = f"/api/companies/{cid}"

    # empty overview: zeros, no fabricated figures
    ov = c.get(f"{base}/accounting/overview").json()
    assert ov["pending_review"] == 0 and ov["validated"] == 0 and ov["possible_duplicates"] == 0

    # upload invoices
    r = upload(c, cid, "purchases.csv", INVOICES)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "processed" and doc["record_count"] == 3

    recs = {x["doc_number"]: x for x in _records(c, cid)}
    inv1 = c.get(f"{base}/records/{recs['INV-001']['id']}").json()
    e1 = inv1["entries"][0]
    assert e1["status"] == "validated", e1["validation"]
    assert e1["total_debit"] == e1["total_credit"] == "1180.00"
    assert "Rent" in e1["explanation"]["accounts"][0]["reason"]

    # INV-002: inter-state supplier, IGST, no category -> needs review with a question
    inv2 = c.get(f"{base}/records/{recs['INV-002']['id']}").json()
    e2 = inv2["entries"][0]
    assert e2["status"] == "needs_review" and e2["explanation"]["question"]

    # credit note linked to original invoice
    cn = c.get(f"{base}/records/{recs['CN-1']['id']}").json()
    assert any(rel["relation"] == "original_invoice" and rel["doc_number"] == "INV-001" for rel in cn["related"])
    assert {v["rule_id"]: v["status"] for v in cn["entries"][0]["validation"]}["NOTE-003"] == "pass"

    # second export containing the same invoice and an altered copy
    r = upload(c, cid, "export2.csv", REPEAT)
    assert r.status_code == 201
    dups = c.get(f"{base}/duplicates").json()
    kinds = sorted((d["kind"], d["strength"]) for d in dups)
    assert ("duplicate", "exact") in kinds and ("possible_alteration", "exact") in kinds
    assert kinds.count(("possible_alteration", "exact")) == 1  # compared with the canonical copy only
    exact = next(d for d in dups if d["kind"] == "duplicate")
    assert "GSTIN" in exact["explanation"] and exact["matched_record_id"] == recs["INV-001"]["id"]

    # the duplicate's entry is blocked pending review
    dup_entry = c.get(f"{base}/records/{exact['record_id']}").json()["entries"][0]
    assert {v["rule_id"]: v["status"] for v in dup_entry["validation"]}["ACC-009"] == "needs_review"

    # confirm duplicate -> its entry is rejected, original untouched
    r = c.post(f"{base}/duplicates/{exact['id']}/decision", json={"decision": "confirmed_duplicate", "note": "Same bill"})
    assert r.status_code == 200, r.text
    assert c.get(f"{base}/records/{exact['record_id']}").json()["record"]["status"] == "duplicate"

    # the altered copy of INV-001 is the wrong copy: the accountant marks it as a duplicate of the original
    alt = next(d for d in dups if d["kind"] == "possible_alteration")
    assert c.post(f"{base}/duplicates/{alt['id']}/decision",
                  json={"decision": "confirmed_duplicate", "note": "Mis-keyed copy"}).status_code == 200

    # correct INV-002 (answer the question by choosing the ledger), revalidate
    accounts = {a["code"]: a["id"] for a in c.get(f"{base}/accounts").json()}
    lines = [{"account_id": accounts["5600"], "debit": "5000.00", "credit": "0"},
             {"account_id": accounts["1302"], "debit": "900.00", "credit": "0"},
             {"account_id": accounts["2100"], "debit": "0", "credit": "5900.00"}]
    r = c.put(f"{base}/entries/{e2['id']}", json={"lines": lines, "note": "Office supplies"})
    assert r.status_code == 200, r.text
    corrected = r.json()
    assert corrected["version"] == e2["version"] + 1
    v = {x["rule_id"]: x["status"] for x in corrected["validation"]}
    assert v["ACC-010"] == "pass" and v["ACC-007"] == "pass"
    # GST-004: Karnataka supplier with IGST, POS 27 -> inter-state passes
    assert v["GST-004"] == "pass"
    assert corrected["status"] == "validated"

    # an incorrect correction (wrong total) is caught even though it balances
    bad = [{"account_id": accounts["5600"], "debit": "5000.00", "credit": "0"},
           {"account_id": accounts["2100"], "debit": "0", "credit": "5000.00"}]
    r = c.put(f"{base}/entries/{e2['id']}", json={"lines": bad})
    assert r.json()["status"] == "needs_review"
    assert any(x["rule_id"] == "ACC-007" and x["status"] == "error" for x in r.json()["validation"])
    # approval refused while errors exist
    r = c.post(f"{base}/entries/{e2['id']}/approve", json={})
    assert r.status_code == 409
    c.put(f"{base}/entries/{e2['id']}", json={"lines": lines})

    # approve and post INV-001; posting is idempotent
    r = c.post(f"{base}/entries/{e1['id']}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    r1 = c.post(f"{base}/entries/{e1['id']}/post")
    r2 = c.post(f"{base}/entries/{e1['id']}/post")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["status"] == "posted" and r2.json()["idempotent_replay"] is True
    assert c.put(f"{base}/entries/{e1['id']}", json={"lines": lines}).status_code == 409  # posted is immutable

    # export the posted journal
    csv_text = c.get(f"{base}/reports/posted-journal.csv").text
    assert "1180.00" in csv_text and csv_text.count("\n") == 5  # header + 4 lines (expense, CGST, SGST, payables)

    # bank statement: payment matched by invoice reference; unknown credit asks
    r = upload(c, cid, "bank.csv", BANK, kind="bank_statement")
    assert r.status_code == 201, r.text
    bank = {x["reference"]: x for x in _records(c, cid, record_type="bank_txn")}
    pay = c.get(f"{base}/records/{bank['UTR100']['id']}").json()["entries"][0]
    assert pay["treatment"] == "bank_supplier_payment" and pay["status"] == "validated"
    unknown = c.get(f"{base}/records/{bank['UTR101']['id']}").json()["entries"][0]
    assert unknown["lines"] == [] and unknown["status"] == "needs_review"
    assert "loan received" in unknown["explanation"]["summary"]

    # reconciliation: approved bank entry reconciles to its line, unknown credit remains unmatched
    c.post(f"{base}/entries/{pay['id']}/approve", json={})
    rec = c.get(f"{base}/bank/reconciliation").json()
    assert any(m["bank_record_id"] == bank["UTR100"]["id"] for m in rec["matched"])
    assert bank["UTR101"]["id"] in rec["unmatched_bank"]

    # audit trail: chained and verifiable, includes the key decisions
    acts = [a["action"] for a in c.get(f"{base}/activity").json()]
    for a in ["document.uploaded", "duplicate.confirmed_duplicate", "entry.corrected", "entry.approved", "entry.posted"]:
        assert a in acts
    assert c.get(f"{base}/activity/verify").json()["valid"] is True

    # overview figures now come from the stored records
    ov = c.get(f"{base}/accounting/overview").json()
    assert ov["posted"] == 1 and ov["approved"] == 1


def test_repeated_identical_upload_not_reprocessed_and_idempotency(owner):
    c, cid = owner["client"], owner["cid"]
    a = upload(c, cid, "p.csv", INVOICES, idempotency_key="abc").json()
    again = upload(c, cid, "p.csv", INVOICES, idempotency_key="abc").json()
    assert again["id"] == a["id"] and again["idempotent_replay"] is True
    b = upload(c, cid, "copy-of-p.csv", INVOICES).json()
    assert b["status"] == "repeated_upload" and b["duplicate_of_id"] == a["id"] and b["record_count"] == 0
    assert len(_records(c, cid)) == 3


def test_record_correction_reproposes_and_needs_review_ack(owner):
    c, cid = owner["client"], owner["cid"]
    base = f"/api/companies/{cid}"
    csv_ = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Taxable Value,CGST,SGST,Total,Category
Purchase,INV-9,10/06/2025,Sharma Supplies,{SUPPLIER_MH},1000.00,90.00,90.00,1180.00,Rent
"""
    upload(c, cid, "x.csv", csv_)
    rec = _records(c, cid)[0]
    e = c.get(f"{base}/records/{rec['id']}").json()["entries"][0]
    v = {x["rule_id"]: x["status"] for x in e["validation"]}
    assert v["GST-004"] == "needs_review"  # place of supply missing
    # approval needs explicit acknowledgement + note
    assert c.post(f"{base}/entries/{e['id']}/approve", json={}).status_code == 409
    assert c.post(f"{base}/entries/{e['id']}/approve", json={"acknowledge_review": True}).status_code == 422
    # fixing the source data resolves it instead
    r = c.patch(f"{base}/records/{rec['id']}", json={"fields": {"place_of_supply": "27"}})
    assert r.status_code == 200
    e2 = r.json()["entries"][0]
    assert e2["status"] == "validated" and e2["version"] == e["version"] + 1
    assert r.json()["record"]["provenance"]["place_of_supply"]["source"] == "user_correction"


def test_failed_upload_explains_and_retry(owner):
    c, cid = owner["client"], owner["cid"]
    r = upload(c, cid, "weird.csv", "foo,bar\n1,2\n")
    assert r.status_code == 201 and r.json()["status"] == "failed"
    assert "column headers" in r.json()["error"]
    assert upload(c, cid, "evil.exe", b"MZ").status_code == 422


def test_policy_must_be_approved_before_use(owner):
    c, cid = owner["client"], owner["cid"]
    base = f"/api/companies/{cid}"
    r = c.post(f"{base}/policies", json={"kind": "bank_keyword", "definition": {"keyword": "SMS CHG", "account_code": "5500",
                                                                                 "direction": "out"},
                                         "rationale": "Bank SMS charges"})
    assert r.status_code == 201
    pol = r.json()
    upload(c, cid, "b.csv", "Date,Description,Debit,Credit\n05/06/2025,SMS CHG QTR,17.70,\n", kind="bank_statement")
    rec = _records(c, cid)[0]
    e = c.get(f"{base}/records/{rec['id']}").json()["entries"][0]
    assert e["lines"] == []  # proposed policy has no effect
    c.post(f"{base}/policies/{pol['id']}/decision", json={"decision": "approve"})
    e = c.post(f"{base}/records/{rec['id']}/repropose").json()
    assert e["treatment"] == "bank_policy" and e["status"] == "validated"
    # invalid policy rejected: ledger must exist
    bad = c.post(f"{base}/policies", json={"kind": "bank_keyword", "definition": {"keyword": "XYZ", "account_code": "0000"},
                                           "rationale": "x"})
    assert bad.status_code == 422


def test_correction_suggests_policy_but_does_not_apply_it(owner):
    c, cid = owner["client"], owner["cid"]
    base = f"/api/companies/{cid}"
    upload(c, cid, "i.csv", f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,Total
Purchase,K-1,10/06/2025,Kaveri Traders,{SUPPLIER_MH},27,100,9,9,118
""")
    rec = _records(c, cid)[0]
    e = c.get(f"{base}/records/{rec['id']}").json()["entries"][0]
    acc = {a["code"]: a["id"] for a in c.get(f"{base}/accounts").json()}
    c.put(f"{base}/entries/{e['id']}", json={"lines": [
        {"account_id": acc["5700"], "debit": "100", "credit": "0"},
        {"account_id": acc["1300"], "debit": "9", "credit": "0"},
        {"account_id": acc["1301"], "debit": "9", "credit": "0"},
        {"account_id": acc["2100"], "debit": "0", "credit": "118"}]})
    pols = c.get(f"{base}/policies").json()
    assert pols and pols[0]["status"] == "proposed" and pols[0]["source"] == "review_correction"
