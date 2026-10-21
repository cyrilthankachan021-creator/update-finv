"""Per-client GST filing frequency (monthly or QRMP) and TDS setup, and what they do to the firm's task list."""
from datetime import date

from app.api.firm import _qrmp_due, tds_position
from app.db import SessionLocal
from app.models import Account, JournalEntry, JournalLine
from tests.conftest import make_gstin


def test_qrmp_due_dates_follow_the_state_of_the_gstin():
    mh, dl = make_gstin("27", "AAACF1234K"), make_gstin("07", "AAACF1234K")
    assert _qrmp_due("2025-09", "GSTR1", mh) == date(2025, 10, 13)
    assert _qrmp_due("2025-09", "GSTR3B", mh) == date(2025, 10, 22)     # Maharashtra: 22nd
    assert _qrmp_due("2025-09", "GSTR3B", dl) == date(2025, 10, 24)     # Delhi: 24th
    assert _qrmp_due("2025-12", "GSTR1", mh) == date(2026, 1, 13)       # rolls into the next year


def test_settings_accept_and_validate_gst_and_tds_setup(owner):
    c, cid = owner["client"], owner["cid"]
    assert c.get(f"/api/companies/{cid}/settings").json()["gst_filing"] == "monthly"
    r = c.patch(f"/api/companies/{cid}/settings", json={"gst_filing": "qrmp", "tds_applicable": True, "tan": "delh12345a"})
    assert r.status_code == 200 and r.json()["gst_filing"] == "qrmp" and r.json()["tan"] == "DELH12345A" and r.json()["tds_applicable"] is True
    assert c.patch(f"/api/companies/{cid}/settings", json={"gst_filing": "weekly"}).status_code == 422
    assert c.patch(f"/api/companies/{cid}/settings", json={"tan": "BAD"}).status_code == 422
    assert c.patch(f"/api/companies/{cid}/settings", json={"tds_applicable": False}).json()["tds_applicable"] is False


def test_a_qrmp_client_only_has_quarter_end_gst_tasks(owner):
    c, cid = owner["client"], owner["cid"]
    monthly = [t for t in c.get("/api/firm/tasks").json()["items"] if t["category"] == "gst"]
    c.patch(f"/api/companies/{cid}/settings", json={"gst_filing": "qrmp"})
    quarterly = [t for t in c.get("/api/firm/tasks").json()["items"] if t["category"] == "gst"]
    assert all(int(t["period"][5:]) in (3, 6, 9, 12) for t in quarterly)
    assert len(quarterly) <= len(monthly) and all("(quarter)" in t["task"] for t in quarterly)


def test_tds_deposit_task_appears_only_for_clients_marked_applicable(owner):
    c, cid = owner["client"], owner["cid"]
    db = SessionLocal()
    try:
        acct = Account(company_id=cid, code="2301", name="TDS Payable", type="liability", role="tds_payable")
        db.add(acct)
        db.flush()
        e = JournalEntry(company_id=cid, entry_date=date.today().replace(day=1), status="posted")
        db.add(e)
        db.flush()
        db.add(JournalLine(entry_id=e.id, account_id=acct.id, debit=0, credit=500))
        db.commit()
        assert tds_position(db, [cid])[cid]["payable"] == 500
    finally:
        db.close()
    assert not [t for t in c.get("/api/firm/tasks").json()["items"] if t["category"] == "tds"]
    c.patch(f"/api/companies/{cid}/settings", json={"tds_applicable": True})
    tds = [t for t in c.get("/api/firm/tasks").json()["items"] if t["category"] == "tds"]
    assert len(tds) == 1 and tds[0]["status"] == "pending" and "500" in tds[0]["issue"]
