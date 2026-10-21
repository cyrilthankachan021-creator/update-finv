"""Approval-gated GST workflow: calculation, approval binding, invalidation, links, jobs, resume and verification.
All data is fictional. No real Telegram or GST portal is contacted."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.engine import gst_returns as gr
from app.models import GstPortalJob, GstReturn, GstReviewLink, JournalEntry, SourceRecord, utcnow
from app.portal import runner
from app.services import gst_filing as gf
from app.services import notify
from tests.conftest import COMPANY_GSTIN, CUSTOMER_MH, SUPPLIER_KA, SUPPLIER_MH, login, register

D = Decimal
PW = "correct-horse-battery"


# ------------------------------------------------------------------ helpers
def add_doc(cid, record_type, number, day, taxable, cgst="0", sgst="0", igst="0", gstin=None, pos="27",
            party="Fictional Party", side=None, entry_status="posted", doc_date=None, original=None) -> int:
    db = SessionLocal()
    tax = D(cgst) + D(sgst) + D(igst)
    r = SourceRecord(company_id=cid, record_type=record_type, note_side=side, party_name=party, party_gstin=gstin,
                     doc_number=number, doc_date=doc_date if doc_date is not None else date(2025, 6, day),
                     place_of_supply=pos, taxable_amount=D(taxable), cgst=D(cgst), sgst=D(sgst), igst=D(igst),
                     cess=D("0"), total_amount=D(taxable) + tax, status=entry_status, original_record_id=original)
    if doc_date == "none":
        r.doc_date = None
    db.add(r)
    db.flush()
    db.add(JournalEntry(company_id=cid, source_record_id=r.id, entry_date=r.doc_date, status=entry_status, version=1))
    db.commit()
    rid = r.id
    db.close()
    return rid


def june_sales(cid) -> dict:
    return {
        "b2b": add_doc(cid, "sales_invoice", "S-1", 3, "10000", "900", "900", gstin=CUSTOMER_MH,
                       party="Mehta Retail"),
        "b2cs": add_doc(cid, "sales_invoice", "S-2", 4, "2000", igst="360", pos="29", party="Walk-in"),
        "b2cl": add_doc(cid, "sales_invoice", "S-3", 5, "150000", igst="27000", pos="29", party="Big Buyer"),
        "cn": add_doc(cid, "credit_note", "SCN-1", 6, "1000", "90", "90", gstin=CUSTOMER_MH, side="sales",
                      party="Mehta Retail"),
        "purchase": add_doc(cid, "purchase_invoice", "P-1", 7, "5000", igst="900", gstin=SUPPLIER_KA,
                            party="Kaveri Traders"),
    }


def set_amount(record_id, taxable):
    db = SessionLocal()
    r = db.get(SourceRecord, record_id)
    r.taxable_amount = D(taxable)
    db.commit()
    db.close()


class FakeSender:
    channel = "telegram"

    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def send(self, text):
        self.sent.append(text)


@pytest.fixture()
def sender():
    s = FakeSender()
    notify.set_sender_override(s)
    yield s
    notify.set_sender_override(None)


def prep(c, cid, rt="GSTR1", period="2025-06"):
    r = c.post(f"/api/companies/{cid}/gst/returns", json={"return_type": rt, "period": period})
    assert r.status_code == 201, r.text
    return r.json()


def approve(c, cid, ret, password=PW, override="HSN summary entered on the portal and checked against sales register"):
    s = ret["snapshot"]
    return c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/approve",
                  json={"snapshot_id": s["id"], "version": s["version"], "payload_sha256": s["payload_sha256"],
                        "password": password, "override_reason": override})


class FakePortal:
    """Stands in for the GST portal. Records every action; has no way to submit or file."""

    def __init__(self, gstin=COMPANY_GSTIN, totals=None, fail_at=None, uploads=None, status="Filed",
                 arn="AA2706250012345"):
        self.gstin, self.totals, self.fail_at, self.status, self.arn = gstin, totals, fail_at, status, arn
        self.uploads = uploads if uploads is not None else []
        self.actions = []

    def _step(self, name):
        self.actions.append(name)
        if self.fail_at == name:
            self.fail_at = None
            raise TimeoutError("portal session expired")

    def open(self): self._step("open")
    def close(self): self.actions.append("close")
    def wait_for_login(self): self._step("login")
    def taxpayer_gstin(self): return self.gstin
    def open_return(self, rt, period): self._step("open_return")

    def gstr1_upload(self, path):
        self._step("upload")
        import json
        with open(path) as f:
            self.last_payload = json.load(f)
        self.uploads.append(f"REF{len(self.uploads) + 1:08d}")
        return self.uploads[-1]

    def latest_upload(self, since):
        self._step("latest_upload")
        return self.uploads[-1] if self.uploads else None

    def upload_status(self, ref):
        self._step("upload_status")
        return "processed"

    def gstr3b_fill(self, payload):
        self._step("fill_3b")
        self.filled = payload

    def summary_totals(self, rt):
        self._step("summary")
        return self.totals

    def filing_status(self, rt, period):
        self._step("filing_status")
        return {"status": self.status, "arn": self.arn, "filed_at": "20-07-2025"}


def snapshot_totals(cid, rid):
    db = SessionLocal()
    ret = db.get(GstReturn, rid)
    t = dict(gf.snapshot(db, ret).totals)
    db.close()
    return t


def run(job_id, portal, worker="w1"):
    db = SessionLocal()
    try:
        return runner.run_job(db, job_id, portal, worker_id=worker, sleep=lambda s: None)
    finally:
        db.close()


def approved_return(owner, sender=None):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    r = approve(c, cid, ret)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ calculation
def test_gstr1_sections_and_totals(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    add_doc(cid, "sales_invoice", "S-JULY", 1, "999", "90", "90", gstin=CUSTOMER_MH, doc_date=date(2025, 7, 1))
    ret = prep(c, cid)
    s = ret["snapshot"]
    b = s["breakdown"]
    assert b["b2b"]["count"] == 1 and b["b2cs"]["count"] == 1 and b["b2cl"]["count"] == 1 and b["cdnr"]["count"] == 1
    # B2B 10000 + B2CS 2000 + B2CL 150000 - credit note 1000; July invoice not included
    assert s["totals"]["taxable"] == "161000.00" and s["totals"]["igst"] == "27360.00"
    assert s["totals"]["cgst"] == "810.00" and s["totals"]["sgst"] == "810.00"
    p = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["snapshot"]
    assert p["version"] == 1
    db = SessionLocal()
    payload = db.get(GstReturn, ret["id"])
    snap = gf.snapshot(db, payload)
    assert snap.payload["fp"] == "062025" and snap.payload["b2b"][0]["ctin"] == CUSTOMER_MH
    assert snap.payload["b2cl"][0]["pos"] == "29" and snap.payload["cdnr"][0]["nt"][0]["ntty"] == "C"
    assert snap.payload["b2cs"][0] == {"sply_ty": "INTER", "pos": "29", "typ": "OE", "rt": 18.0, "txval": 2000.0,
                                       "iamt": 360.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0}
    db.close()
    codes = {w["code"]: w["severity"] for w in s["warnings"]}
    assert codes["HSN_MISSING"] == "blocking" and "PENDING_DOCS" not in codes
    assert s["reconciliation"]["status"] in {"clear", "open_items", "unavailable"}


def test_gstr3b_values(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    add_doc(cid, "purchase_invoice", "P-NOGSTIN", 8, "100", "9", "9", gstin=None, party="Unregistered Vendor")
    ret = prep(c, cid, "GSTR3B")
    t = ret["snapshot"]["totals"]
    assert t["outward_taxable"] == "161000.00" and t["output_igst"] == "27360.00"
    assert t["itc_igst"] == "900.00" and t["itc_cgst"] == "0.00"      # no ITC without a supplier GSTIN
    codes = {w["code"]: w["severity"] for w in ret["snapshot"]["warnings"]}
    assert codes["ITC_EXCLUDED"] == "info" and codes["ITC_NOT_MATCHED_2B"] == "blocking"
    assert ret["snapshot"]["breakdown"]["3.2"]["29"] == {"taxable": "152000.00", "igst": "27360.00"}


def test_unapproved_and_undated_documents_block(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    add_doc(cid, "sales_invoice", "S-PENDING", 9, "500", "45", "45", gstin=CUSTOMER_MH, entry_status="needs_review")
    add_doc(cid, "sales_invoice", "S-NODATE", 1, "500", "45", "45", gstin=CUSTOMER_MH, doc_date="none")
    w = {x["code"]: x for x in prep(c, cid)["snapshot"]["warnings"]}
    assert w["PENDING_DOCS"]["severity"] == "blocking" and w["PENDING_DOCS"]["count"] == 1
    assert w["UNDATED_DOCS"]["count"] == 1


def test_multi_rate_document_blocks():
    rows = [gr.DocRow(1, "sales_invoice", None, "posted", 1, 1, "posted", CUSTOMER_MH, "X", date(2025, 6, 1), "27",
                      D("1000"), D("70"), D("70"), D("0"), D("0"), D("1140"))]
    res = gr.build_gstr1(COMPANY_GSTIN, "2025-06", rows, [])
    assert {w["code"] for w in res.blocking} >= {"RATE_UNKNOWN"} and res.record_ids == []


def test_preparing_twice_without_changes_keeps_version(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    a, b = prep(c, cid), prep(c, cid)
    assert a["snapshot"]["id"] == b["snapshot"]["id"] and b["snapshot"]["version"] == 1


def test_company_needs_valid_gstin(app_client):
    me = register(app_client)
    cid = app_client.post("/api/companies", json={"organization_id": me["organizations"][0]["id"],
                                                  "name": "No GSTIN Co"}).json()["id"]
    r = app_client.post(f"/api/companies/{cid}/gst/returns", json={"return_type": "GSTR1", "period": "2025-06"})
    assert r.status_code == 422 and "GSTIN" in r.json()["detail"]


# ------------------------------------------------------------------ notification and links
def test_message_is_minimal_and_link_single_use(owner, sender):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    r = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/send")
    assert r.status_code == 200 and r.json()["state"] == "awaiting_approval"
    msg = sender.sent[0]
    for secret in ("Sample Traders", "Mehta Retail", "Kaveri", "Big Buyer", COMPANY_GSTIN, CUSTOMER_MH, "S-1",
                   "10000", "161000", "27360"):
        assert secret not in msg, secret
    assert COMPANY_GSTIN[-4:] in msg and "2025-06" in msg and f"Client #{cid}" in msg
    token = msg.split("?t=")[1].split()[0]
    # the token is stored only as a hash
    db = SessionLocal()
    assert token not in str(db.scalars(select(GstReviewLink.token_hash)).all())
    db.close()

    first = c.post("/api/gst/review-links/redeem", json={"token": token})
    assert first.status_code == 200 and first.json() == {"company_id": cid, "return_id": ret["id"],
                                                         "snapshot_is_current": True}
    assert c.post("/api/gst/review-links/redeem", json={"token": token}).status_code == 410     # replay
    # redeeming never approves
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "awaiting_approval"


def test_link_needs_signin_membership_and_expires(owner, sender):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/send")
    c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/send")
    t1, t2 = (m.split("?t=")[1].split()[0] for m in sender.sent)
    c.post("/api/auth/logout")
    assert c.post("/api/gst/review-links/redeem", json={"token": t1}).status_code == 401     # public link alone: no
    register(c, email="outsider@example.test", org="Other Firm")
    assert c.post("/api/gst/review-links/redeem", json={"token": t1}).status_code == 404     # not a member
    c.post("/api/auth/logout")
    login(c, "owner@example.test")
    db = SessionLocal()
    for link in db.scalars(select(GstReviewLink)).all():
        link.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()
    assert c.post("/api/gst/review-links/redeem", json={"token": t2}).status_code == 410
    assert c.post("/api/gst/review-links/redeem", json={"token": "x" * 43}).status_code == 404


def test_message_check_refuses_client_data():
    with pytest.raises(notify.MessageNotAllowed):
        notify.check_message(f"GSTIN {COMPANY_GSTIN}", [])
    with pytest.raises(notify.MessageNotAllowed):
        notify.check_message("Mehta Retail owes tax", ["Mehta Retail"])
    with pytest.raises(notify.MessageNotAllowed):
        notify.check_message("enter the OTP", [])


def test_without_telegram_approval_still_works_in_app(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    r = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/send")
    assert r.status_code == 200 and r.json()["telegram_configured"] is False
    db = SessionLocal()
    assert db.scalars(select(GstReviewLink)).all() == []
    db.close()


# ------------------------------------------------------------------ approval
def test_approval_rules(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    assert approve(c, cid, ret, password="wrong").status_code == 403
    assert approve(c, cid, ret, override=None).status_code == 409             # blocking warning, no override
    assert approve(c, cid, ret, override="ok").status_code == 422              # override needs a real reason
    stale = dict(ret, snapshot=dict(ret["snapshot"], version=99))
    assert approve(c, cid, stale).status_code == 409                          # not the version reviewed
    other = dict(ret, snapshot=dict(ret["snapshot"], payload_sha256="0" * 64))
    assert approve(c, cid, other).status_code == 409
    r = approve(c, cid, ret)
    assert r.status_code == 200 and r.json()["state"] == "approved"
    assert r.json()["override"]["warnings"] == ["HSN_MISSING"]
    assert approve(c, cid, ret).status_code == 409                            # already approved
    acts = [h["action"] for h in r.json()["history"]]
    assert "gst.approved" in acts and "gst.approval_refused" in acts


def test_accountant_and_reviewer_permissions(owner):
    c, cid, org = owner["client"], owner["cid"], owner["org_id"]
    june_sales(cid)
    ret = prep(c, cid)
    c.post("/api/auth/logout")
    register(c, email="acct@example.test", org="a")
    register(c, email="rev@example.test", org="b")
    login(c, "owner@example.test")
    c.post(f"/api/organizations/{org}/members", json={"email": "acct@example.test", "role": "accountant"})
    c.post(f"/api/organizations/{org}/members", json={"email": "rev@example.test", "role": "reviewer"})
    c.post("/api/auth/logout")
    login(c, "acct@example.test")
    assert approve(c, cid, ret).status_code == 403
    c.post("/api/auth/logout")
    login(c, "rev@example.test")
    r = approve(c, cid, ret)
    assert r.status_code == 403 and "owner or admin" in r.json()["detail"]      # reviewer cannot override
    c.post("/api/auth/logout")


def test_concurrent_approvals_only_one_wins(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    rid = prep(c, cid)["id"]
    db1, db2 = SessionLocal(), SessionLocal()
    r1, r2 = db1.get(GstReturn, rid), db2.get(GstReturn, rid)
    gf.transition(db1, r1, "approved", None, "first")
    db1.commit()
    with pytest.raises(gf.FilingError, match="same moment"):
        gf.transition(db2, r2, "approved", None, "second")
    db1.close(), db2.close()


# ------------------------------------------------------------------ invalidation
def test_data_change_invalidates_approval_and_blocks_portal(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    db = SessionLocal()
    rid = db.scalar(select(SourceRecord.id).where(SourceRecord.doc_number == "S-1"))
    db.close()
    set_amount(rid, "10001")
    r = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job")
    assert r.status_code == 409
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "invalidated" and d["approved_snapshot_id"] is None and "changed" in d["reason"]
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}/payload.json").status_code == 409
    # prepare again -> version 2, must be approved again
    again = prep(c, cid)
    assert again["snapshot"]["version"] == 2 and again["state"] == "prepared"
    assert again["snapshot"]["totals"]["taxable"] == "161001.00"


def test_entry_status_change_also_invalidates(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    db = SessionLocal()
    e = db.scalar(select(JournalEntry).join(SourceRecord, JournalEntry.source_record_id == SourceRecord.id)
                  .where(SourceRecord.doc_number == "S-2"))
    e.version = 2
    db.commit()
    db.close()
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "invalidated"


# ------------------------------------------------------------------ portal jobs
def test_portal_job_runs_and_stops_before_filing(owner, sender):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}/payload.json").status_code == 200
    d = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()
    assert d["state"] == "portal_in_progress" and "gst_portal.py" in d["prepare_job"]["command"]
    assert c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").status_code == 409   # no duplicate job
    portal = FakePortal(totals=snapshot_totals(cid, ret["id"]))
    assert run(d["prepare_job"]["id"], portal) == "done"
    assert portal.actions.count("upload") == 1 and portal.last_payload["gstin"] == COMPANY_GSTIN
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "portal_ready_for_review" and d["arn"] is None
    assert d["prepare_job"]["steps"] == ["period_opened", "taxpayer_confirmed", "totals_read", "upload_processed",
                                         "upload_started", "upload_submitted"]
    assert "file it yourself" in sender.sent[-1]
    # the upload file was deleted after the upload
    assert not list((settings.data_dir / "gst_uploads").glob("*.json"))


def test_wrong_taxpayer_stops(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    portal = FakePortal(gstin=SUPPLIER_MH)
    assert run(job["id"], portal) == "failed"
    assert "upload" not in portal.actions
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "portal_failed" and "different taxpayer" in d["reason"]


def test_totals_mismatch_fails(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    totals = snapshot_totals(cid, ret["id"]) | {"igst": "1.00"}
    assert run(job["id"], FakePortal(totals=totals)) == "failed"
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "portal_failed"
    assert d["prepare_job"]["differences"] == [{"field": "igst", "finv": "27360.00", "portal": "1.00"}]


def test_resume_after_expired_session_does_not_upload_twice(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    uploads: list = []
    first = FakePortal(totals=snapshot_totals(cid, ret["id"]), fail_at="upload_status", uploads=uploads)
    assert run(job["id"], first) == "failed"
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "portal_failed"
    d = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()     # resume
    assert d["prepare_job"]["id"] == job["id"]
    second = FakePortal(totals=snapshot_totals(cid, ret["id"]), uploads=uploads)
    assert run(job["id"], second, worker="w2") == "done"
    assert len(uploads) == 1 and "upload" not in second.actions


def test_crash_between_upload_and_checkpoint_adopts_existing_upload(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    db = SessionLocal()
    j = db.get(GstPortalJob, job["id"])
    j.checkpoints = {"taxpayer_confirmed": "x", "period_opened": "x", "upload_started": utcnow().isoformat()}
    db.commit()
    db.close()
    portal = FakePortal(totals=snapshot_totals(cid, ret["id"]), uploads=["REF00000077"])
    assert run(job["id"], portal) == "done"
    assert "upload" not in portal.actions and "latest_upload" in portal.actions


def test_second_worker_cannot_take_a_live_job(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    db = SessionLocal()
    gf.claim_job(db, job["id"], "w1")
    with pytest.raises(gf.FilingError, match="already running"):
        gf.claim_job(SessionLocal(), job["id"], "w2")
    j = db.get(GstPortalJob, job["id"])
    j.heartbeat_at = utcnow() - timedelta(minutes=10)     # w1 died: its lease expires
    db.commit()
    gf.claim_job(SessionLocal(), job["id"], "w2")
    db.close()


def test_data_change_during_job_stops_it(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    db = SessionLocal()
    rid = db.scalar(select(SourceRecord.id).where(SourceRecord.doc_number == "S-2"))
    db.close()

    class ChangingPortal(FakePortal):
        def open_return(self, rt, period):
            super().open_return(rt, period)
            set_amount(rid, "2500")

    portal = ChangingPortal(totals=snapshot_totals(cid, ret["id"]))
    assert run(job["id"], portal) == "failed"
    assert "upload" not in portal.actions
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "invalidated"


def test_gstr3b_job_fills_values(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid, "GSTR3B")
    ret = approve(c, cid, ret, override="ITC checked against GSTR-2B on the portal for June").json()
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    t = snapshot_totals(cid, ret["id"])
    portal = FakePortal(totals={k: v for k, v in t.items() if k not in {"documents", "nil_exempt"}})
    assert run(job["id"], portal) == "done"
    assert portal.filled["sup_details"]["osup_det"]["txval"] == 161000.0


# ------------------------------------------------------------------ filing and verification
def test_filing_is_verified_only_from_portal(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    assert c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/filed", json={}).status_code == 409   # too early
    run(job["id"], FakePortal(totals=snapshot_totals(cid, ret["id"])))
    d = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/filed", json={"arn": "AA2706250012345"}).json()
    assert d["state"] == "filing_reported" and d["arn"] is None and d["reported_arn"] == "AA2706250012345"
    vjob = d["verify_job"]
    # portal does not show it as filed yet -> not verified
    assert run(vjob["id"], FakePortal(status="Not Filed", arn=None)) == "failed"
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "filing_reported"
    d = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/filed", json={}).json()       # check again
    assert run(d["verify_job"]["id"], FakePortal()) == "done"
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "verified" and d["arn"] == "AA2706250012345" and d["verified_at"]
    # filed returns cannot be prepared again or invalidated by later edits
    assert c.post(f"/api/companies/{cid}/gst/returns", json={"return_type": "GSTR1", "period": "2025-06"}).status_code == 409
    db = SessionLocal()
    rid = db.scalar(select(SourceRecord.id).where(SourceRecord.doc_number == "S-1"))
    db.close()
    set_amount(rid, "1")
    assert c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["state"] == "verified"
    acts = [a["action"] for a in c.get(f"/api/companies/{cid}/activity").json()]
    for a in ("gst.prepared", "gst.approved", "gst.portal_in_progress", "gst.portal_ready_for_review",
              "gst.filing_reported", "gst.verification_pending", "gst.verified"):
        assert a in acts, a
    assert c.get(f"/api/companies/{cid}/activity/verify").json()["valid"] is True


def test_cancel_needs_reason(owner):
    c, cid = owner["client"], owner["cid"]
    june_sales(cid)
    ret = prep(c, cid)
    assert c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/cancel", json={"reason": " "}).status_code == 422
    r = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/cancel", json={"reason": "Prepared by mistake"})
    assert r.json()["state"] == "cancelled"


def test_portal_window_opens_for_the_due_job(owner, monkeypatch):
    from app.portal import launch
    opened: list = []
    monkeypatch.setattr(launch, "can_launch", lambda: True)
    monkeypatch.setattr(launch, "launch", lambda job_id: opened.append(job_id) or True)
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    base = f"/api/companies/{cid}/gst/returns/{ret['id']}"
    assert c.post(f"{base}/open-window").status_code == 409                      # nothing due yet
    d = c.post(f"{base}/portal-job").json()
    assert d["window_opened"] is True and opened == [d["prepare_job"]["id"]] and d["can_open_window"] is True
    assert c.post(f"{base}/open-window").json()["window_opened"] is True         # reopen after closing it
    assert opened == [d["prepare_job"]["id"]] * 2
    run(d["prepare_job"]["id"], FakePortal(totals=snapshot_totals(cid, ret["id"])))
    assert c.post(f"{base}/open-window").status_code == 409                      # ready for review: nothing to open
    v = c.post(f"{base}/filed", json={}).json()
    assert v["window_opened"] is True and opened[-1] == v["verify_job"]["id"]


def test_no_window_without_a_screen(owner):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    d = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()
    assert d["window_opened"] is False and d["can_open_window"] is False
    r = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/open-window")
    assert r.status_code == 409 and "command" in r.json()["detail"]
