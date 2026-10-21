"""Audit workspace workflow: import, checks, vouching, exceptions, reports."""
from tests.conftest import SUPPLIER_MH, upload

TB = """Account Code,Account Name,Type,Opening Balance,Debit,Credit,Closing Balance
1100,Bank Account,Asset,50000.00,120000.00,90000.00,80000.00
2100,Trade Payables,Liability,-30000.00,40000.00,60000.00,-50000.00
4100,Sales,Income,0,0,150000.00,-140000.00
5200,Rent,Expense,0,120000.00,0,120000.00
5600,Office Expenses,Expense,-20000.00,20000.00,0,0
3100,Capital Account,Equity,0,0,0,0
"""
LEDGER = """Date,Account Code,Account Name,Voucher No,Party,Narration,Debit,Credit
05/06/2025,5200,Rent,JV-10,Landlord Estates,June rent,60000.00,0
05/06/2025,5200,Rent,JV-10,Landlord Estates,June rent,60000.00,0
08/06/2025,5600,Office Expenses,JV-11,Sharma Supplies,INV-001 stationery,1180.00,0
"""
INV = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,Total
Purchase,INV-001,07/06/2025,Sharma Supplies,{SUPPLIER_MH},27,1000.00,90.00,90.00,1180.00
Purchase,R-6,01/06/2025,Landlord Estates,{SUPPLIER_MH},27,50000.00,4500.00,4500.00,59000.00
"""


def test_audit_workflow(owner):
    c, cid = owner["client"], owner["cid"]
    base = f"/api/companies/{cid}/audit"
    ov = c.get(f"{base}/overview").json()
    assert ov["imports"] == [] and ov["awaiting_review"] == 0

    r = upload(c, cid, "tb.csv", TB, kind="trial_balance", workspace="audit", period_label="FY 2025-26",
               period_start="2025-04-01", period_end="2026-03-31")
    assert r.status_code == 201, r.text
    tb_id = c.get(f"{base}/overview").json()["imports"][0]["id"]
    tb = c.get(f"{base}/imports/{tb_id}").json()
    rules = {x["rule_id"] for x in tb["exceptions"]}
    # Sales closing was mis-keyed (-1,40,000 instead of -1,50,000): TB does not agree and does not roll forward
    assert {"TB-001", "TB-002"} <= rules
    assert tb["summary"]["difference"] == "10000.00"
    assert tb["summary"]["lines"] == 6

    r = upload(c, cid, "gl.csv", LEDGER, kind="ledger", workspace="audit", period_start="2025-04-01", period_end="2026-03-31")
    assert r.status_code == 201, r.text
    led = c.get(f"{base}/ledger").json()
    assert led["total"] == 3
    exs = c.get(f"{base}/exceptions", params={"status": "unresolved"}).json()
    rules = {x["rule_id"] for x in exs}
    assert {"LED-001", "VCH-001", "LED-004"} <= rules

    # vouching: link the invoice to the stationery entry -> consistent; rent entry vs wrong invoice -> discrepancy
    upload(c, cid, "inv.csv", INV)
    recs = {x["doc_number"]: x for x in c.get(f"/api/companies/{cid}/records").json()["items"]}
    entries = {e["voucher_no"] + str(e["row_index"]): e for e in led["items"]}
    stat = entries["JV-113"]
    rent = entries["JV-101"]
    v1 = c.post(f"{base}/ledger/{stat['id']}/evidence", json={"record_id": recs["INV-001"]["id"]}).json()
    assert v1["outcome"] == "consistent", v1
    v2 = c.post(f"{base}/ledger/{rent['id']}/evidence", json={"record_id": recs["R-6"]["id"]}).json()
    assert v2["outcome"] == "discrepancy" and "amount" in v2["result"]["summary"]
    assert c.post(f"{base}/ledger/{rent['id']}/evidence", json={"record_id": recs["R-6"]["id"]}).status_code == 409
    # evidence reuse flagged
    other_rent = entries["JV-102"]
    v3 = c.post(f"{base}/ledger/{other_rent['id']}/evidence", json={"record_id": recs["R-6"]["id"]}).json()
    assert any(ch["check"] == "evidence_reuse" for ch in v3["result"]["checks"])

    # linked entries no longer have missing-evidence exceptions open
    detail = c.get(f"{base}/ledger/{stat['id']}").json()
    assert all(x["status"] != "open" or x["rule_id"] != "VCH-001" for x in detail["exceptions"])

    # exception workflow
    ex = next(x for x in c.get(f"{base}/exceptions", params={"status": "unresolved"}).json() if x["rule_id"] == "LED-001")
    assert c.post(f"{base}/exceptions/{ex['id']}", json={"status": "resolved"}).status_code == 422  # needs explanation
    r = c.post(f"{base}/exceptions/{ex['id']}", json={"status": "needs_information", "comment": "Asked client for both vouchers"})
    assert r.status_code == 200 and r.json()["status"] == "needs_information"
    r = c.post(f"{base}/exceptions/{ex['id']}", json={"status": "accepted_with_explanation",
                                                      "comment": "Two separate months paid together; confirmed with bank"})
    hist = r.json()["history"]
    assert [h["to_status"] for h in hist] == ["needs_information", "accepted_with_explanation"]

    # vouching review decision
    r = c.post(f"{base}/vouching/{v2['id']}/review", json={"review_status": "queried", "comment": "Invoice is 59,000"})
    assert r.json()["review_status"] == "queried"

    # audit work never modified accounting records
    assert c.get(f"/api/companies/{cid}/entries", params={"status": "posted"}).json()["total"] == 0

    # reports distinguish detected anomalies from resolved ones
    rep = c.get(f"{base}/reports/exceptions.csv").text
    assert "detected anomaly - unresolved" in rep and "accepted with explanation" in rep and "not a statutory audit" in rep
    assert "queried" in c.get(f"{base}/reports/vouching.csv").text
    assert c.get(f"/api/companies/{cid}/activity/verify").json()["valid"]


def test_balanced_tb_and_rerun_is_idempotent(owner):
    c, cid = owner["client"], owner["cid"]
    tb = "Account,Debit,Credit\nBank Account,1000,0\nCapital Account,0,1000\n"
    upload(c, cid, "tb.csv", tb, kind="trial_balance", workspace="audit")
    imp = c.get(f"/api/companies/{cid}/audit/overview").json()["imports"][0]
    assert imp["summary"]["difference"] == "0.00"
    n1 = len(c.get(f"/api/companies/{cid}/audit/exceptions").json())
    c.post(f"/api/companies/{cid}/audit/imports/{imp['id']}/run-checks")
    assert len(c.get(f"/api/companies/{cid}/audit/exceptions").json()) == n1


def test_accountant_cannot_record_audit_decisions(owner):
    from tests.conftest import login, register
    c, cid, org = owner["client"], owner["cid"], owner["org_id"]
    upload(c, cid, "gl.csv", LEDGER, kind="ledger", workspace="audit")
    ex = c.get(f"/api/companies/{cid}/audit/exceptions").json()[0]
    c.post("/api/auth/logout")
    register(c, email="acct2@example.test", org="tmp")
    login(c, "owner@example.test")
    c.post(f"/api/organizations/{org}/members", json={"email": "acct2@example.test", "role": "accountant"})
    c.post("/api/auth/logout")
    login(c, "acct2@example.test")
    assert c.post(f"/api/companies/{cid}/audit/exceptions/{ex['id']}", json={"comment": "x"}).status_code == 403
