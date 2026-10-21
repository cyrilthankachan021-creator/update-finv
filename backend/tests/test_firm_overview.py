"""Firm dashboard and client separation: real data only, scoped to the signed-in user's firm and to one client at a time."""
from datetime import date

from app.api.firm import financial_year
from tests.conftest import SUPPLIER_MH, login, register, upload

BANK = "Date,Description,Debit,Credit,Balance\n2026-04-05,UPI to Vendor,5000.00,,95000.00\n"
INV = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,Total,Category
Purchase,INV-001,10/06/2025,Sharma Supplies,{SUPPLIER_MH},27,1000.00,90.00,90.00,1180.00,Rent
"""


def add_client(c, org_id, name, **kw):
    r = c.post("/api/companies", json={"organization_id": org_id, "name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_financial_year_runs_april_to_march():
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"


def test_empty_firm_has_no_clients_and_no_invented_work(app_client):
    register(app_client)
    body = app_client.get("/api/firm/overview").json()
    assert body["attention"] == [] and body["top_issues"] == []
    assert all(v == 0 for v in body["summary"].values())
    assert body["not_tracked"]            # the view says what it cannot cover
    for path in ("clients", "tasks", "issues", "imports"):
        assert app_client.get(f"/api/firm/{path}").json()["items"] == []


def test_requires_sign_in(app_client):
    for path in ("overview", "clients", "tasks", "issues", "imports", "clients/1"):
        assert app_client.get(f"/api/firm/{path}").status_code == 401, path


def test_registered_client_gets_gst_tasks_and_missing_data_issue(owner):
    c = owner["client"]
    gst = [t for t in c.get("/api/firm/tasks").json()["items"] if t["category"] == "gst"]
    assert gst and all(t["client_id"] == owner["cid"] and t["financial_year"] for t in gst)
    assert min(t["period"] for t in gst) >= date.today().strftime("%Y-%m")     # nothing before the client joined
    assert all(t["status"] == "not_started" for t in gst)
    issues = c.get("/api/firm/issues").json()["items"]
    assert any(i["category"] == "missing_data" and i["client_id"] == owner["cid"] for i in issues)
    assert c.get("/api/firm/overview").json()["summary"]["missing_data"] == 1


def test_client_without_gstin_has_no_gst_tasks(owner):
    c = owner["client"]
    other = add_client(c, owner["org_id"], "Unregistered Co")
    assert not [t for t in c.get("/api/firm/tasks").json()["items"] if t["client_id"] == other and t["category"] == "gst"]


def test_missing_data_belongs_to_the_right_client_only(owner):
    """One client importing data must not hide, or cause, another client's missing-data flag."""
    c, a = owner["client"], owner["cid"]
    b = add_client(c, owner["org_id"], "Second Client Ltd")
    assert upload(c, a, "bank.csv", BANK, kind="bank_statement").status_code == 201
    flagged = {i["client_id"] for i in c.get("/api/firm/issues?category=missing_data").json()["items"]}
    assert flagged == {b}
    imports = c.get("/api/firm/imports").json()["items"]
    assert imports and {i["client_id"] for i in imports} == {a}
    assert c.get(f"/api/firm/imports?client_id={b}").json()["items"] == []


def test_client_list_filters_sorts_searches_and_paginates(owner):
    c, org = owner["client"], owner["org_id"]
    ids = [add_client(c, org, f"Client {n:02d}") for n in range(30)]
    page1 = c.get("/api/firm/clients?page_size=10").json()
    assert page1["total"] == 31 and len(page1["items"]) == 10 and page1["page"] == 1
    page4 = c.get("/api/firm/clients?page_size=10&page=4").json()
    assert len(page4["items"]) == 1
    row = c.get(f"/api/firm/clients?q=Client 07").json()["items"]
    assert [r["id"] for r in row] == [ids[7]]
    assert row[0]["financial_year"] == financial_year(date.today()) and row[0]["work_status"] == "missing_data"
    missing = c.get("/api/firm/clients?filter=missing_data&page_size=100").json()
    assert missing["total"] == 31
    assert c.get("/api/firm/clients?filter=overdue").json()["total"] == 0       # nothing is overdue for clients that joined today
    assert c.get("/api/firm/clients?page_size=500").status_code == 422          # bounded page size


def test_task_and_issue_filters(owner):
    c = owner["client"]
    tasks = c.get("/api/firm/tasks").json()
    fy = tasks["facets"]["financial_years"][0]
    assert c.get(f"/api/firm/tasks?financial_year={fy}").json()["total"] == tasks["total"]
    assert c.get("/api/firm/tasks?financial_year=1999-00").json()["total"] == 0
    assert c.get("/api/firm/tasks?status=completed").json()["total"] == 0
    assert c.get("/api/firm/issues?severity=high").json()["total"] == 0


def test_client_workspace_summary_is_that_clients_only(owner):
    c, a = owner["client"], owner["cid"]
    b = add_client(c, owner["org_id"], "Second Client Ltd", gstin=SUPPLIER_MH)
    body = c.get(f"/api/firm/clients/{b}").json()
    assert body["client"]["id"] == b
    assert {t["client_id"] for t in body["tasks"]} <= {b} and {i["client_id"] for i in body["issues"]} == {b}
    assert c.get(f"/api/firm/tasks?client_id={a}").json()["items"]
    assert {t["client_id"] for t in c.get(f"/api/firm/tasks?client_id={a}").json()["items"]} == {a}


def test_another_firms_clients_are_never_reachable(owner):
    c, mine = owner["client"], owner["cid"]
    c.post("/api/auth/logout")
    other = register(c, email="other@example.test", org="Other CA")
    theirs = add_client(c, other["organizations"][0]["id"], "Other Firm Client", gstin=SUPPLIER_MH)
    assert [x["name"] for x in c.get("/api/firm/clients").json()["items"]] == ["Other Firm Client"]
    # asking for my firm's client by id, through any filter, finds nothing and leaks nothing
    assert c.get(f"/api/firm/clients/{mine}").status_code == 404
    assert c.get(f"/api/firm/tasks?client_id={mine}").status_code == 404
    assert c.get(f"/api/firm/issues?client_id={mine}").status_code == 404
    assert c.get(f"/api/firm/imports?client_id={mine}").status_code == 404
    assert "Sample Traders Pvt Ltd" not in c.get("/api/firm/tasks?page_size=100").text
    c.post("/api/auth/logout")
    login(c, "owner@example.test")
    assert c.get(f"/api/firm/clients/{theirs}").status_code == 404
    assert "Other Firm Client" not in c.get("/api/firm/issues?page_size=100").text


def test_records_from_one_client_are_not_reachable_through_another(owner):
    """Same firm, two clients: an id from client A used under client B's URL is a 404 for every kind of object."""
    c, a = owner["client"], owner["cid"]
    b = add_client(c, owner["org_id"], "Second Client Ltd")
    upload(c, a, "i.csv", INV)
    doc = c.get(f"/api/companies/{a}/documents").json()[0]["id"]
    rec = c.get(f"/api/companies/{a}/records").json()["items"][0]["id"]
    ent = c.get(f"/api/companies/{a}/records/{rec}").json()["entries"][0]["id"]
    for path in (f"documents/{doc}", f"documents/{doc}/file", f"records/{rec}", f"entries/{ent}"):
        assert c.get(f"/api/companies/{a}/{path}").status_code == 200, path
        assert c.get(f"/api/companies/{b}/{path}").status_code == 404, path
    assert c.post(f"/api/companies/{b}/entries/{ent}/approve", json={}).status_code == 404
    assert c.get(f"/api/companies/{b}/records").json()["items"] == []
    assert c.get(f"/api/companies/{b}/documents").json() == []
    assert c.get(f"/api/companies/{b}/assistant/messages?workspace=accounting").json() == []
    own = c.get(f"/api/companies/{a}/reports/journal.csv")
    assert own.status_code == 200 and "Rent" in own.text                       # control: the report does contain A's entry
    other = c.get(f"/api/companies/{b}/reports/journal.csv")
    assert other.status_code == 200 and "Rent" not in other.text and "INV-001" not in other.text


def test_a_big_firm_gets_a_short_cache_and_a_small_firm_never_does(owner, monkeypatch):
    from app.api import firm
    c = owner["client"]
    firm._cache.clear()
    assert c.get("/api/firm/overview").json()["summary"]["clients"] == 1
    add_client(c, owner["org_id"], "Second Co")
    assert c.get("/api/firm/overview").json()["summary"]["clients"] == 2          # small firm: always fresh
    monkeypatch.setattr(firm, "BIG_FIRM_CLIENTS", 2)
    firm._cache.clear()
    assert c.get("/api/firm/overview").json()["summary"]["clients"] == 2
    add_client(c, owner["org_id"], "Third Co")
    assert c.get("/api/firm/overview").json()["summary"]["clients"] == 2          # big firm: served from the cache
    firm._cache.clear()
    assert c.get("/api/firm/overview").json()["summary"]["clients"] == 3


def test_a_client_with_no_data_has_an_upload_task_that_the_missing_documents_filter_finds(owner):
    c = owner["client"]
    got = c.get("/api/firm/tasks?status=missing_documents").json()["items"]
    assert [t["client_id"] for t in got] == [owner["cid"]] and got[0]["task"] == "Upload accounting data"
    assert upload(c, owner["cid"], "bank.csv", BANK, kind="bank_statement").status_code == 201
    assert c.get("/api/firm/tasks?status=missing_documents").json()["items"] == []


def test_creating_the_same_client_twice_is_refused_but_other_firms_may_use_the_name(owner, app_client):
    c, org = owner["client"], owner["org_id"]
    from tests.conftest import make_gstin
    gstin = make_gstin("29", "DDDDE3456N")
    first = c.post("/api/companies", json={"organization_id": org, "name": "Acme Traders", "gstin": gstin, "gst_filing": "qrmp", "tds_applicable": True, "tan": "delh12345a"})
    assert first.status_code == 201, first.text
    st = c.get(f"/api/companies/{first.json()['id']}/settings").json()
    assert (st["gst_filing"], st["tds_applicable"], st["tan"]) == ("qrmp", True, "DELH12345A")
    assert c.post("/api/companies", json={"organization_id": org, "name": "ACME TRADERS"}).status_code == 409       # same name, any case
    assert c.post("/api/companies", json={"organization_id": org, "name": "Different Name", "gstin": gstin}).status_code == 409


def test_audit_issues_carry_expected_and_actual_values_period_and_source(owner):
    c, cid = owner["client"], owner["cid"]
    tb = "Account Code,Account Name,Opening,Debit,Credit,Closing\n1000,Cash,100.00,50.00,10.00,999.00\n3000,Capital,-100.00,0,0,-100.00\n"
    r = upload(c, cid, "tb.csv", tb, kind="trial_balance", workspace="audit", period_label="FY 2025-26", period_start="2025-04-01", period_end="2026-03-31")
    assert r.status_code == 201, r.text
    issues = [i for i in c.get("/api/firm/issues?category=audit_exception").json()["items"]]
    roll = next(i for i in issues if "TB-002" in i["detection"])
    assert (roll["expected"], roll["actual"]) == ("140.00", "999.00")
    assert roll["period"] == "FY 2025-26" and roll["financial_year"] == "2025-26" and roll["source"]["import_id"]
    assert c.get("/api/firm/issues?period=2025-26").json()["total"] >= 1
    assert c.get("/api/firm/issues?period=FY 2030").json()["items"] == []
