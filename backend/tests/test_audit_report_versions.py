"""Audit report versions: frozen snapshots, sign-off only on an unchanged newest draft, and who may do it."""
from app.db import SessionLocal
from app.models import AuditException
from tests.conftest import login, register, upload

TB = ("Account Code,Account Name,Type,Debit,Credit\n1000,Cash,asset,5000,0\n3000,Capital,equity,0,5000\n")


def base(c, cid):
    return f"/api/companies/{cid}/audit/report-versions"


def add_exception(cid, status="open"):
    db = SessionLocal()
    try:
        db.add(AuditException(company_id=cid, object_type="account", object_id=1, rule_id="TB-X", severity="high", title="Odd balance",
                              explanation="x", evidence={}, fingerprint=f"fp-{status}-{cid}", status=status))
        db.commit()
    finally:
        db.close()


def test_a_version_is_a_numbered_frozen_snapshot_and_new_versions_supersede_drafts(owner):
    c, cid = owner["client"], owner["cid"]
    v1 = c.post(base(c, cid), json={"note": "first"}).json()
    v2 = c.post(base(c, cid), json={}).json()
    assert (v1["version"], v2["version"]) == (1, 2) and v1["snapshot"]["company"]["id"] == cid
    listed = {v["version"]: v["status"] for v in c.get(base(c, cid)).json()}
    assert listed == {1: "superseded", 2: "draft"}
    r = c.post(f"{base(c, cid)}/{v1['id']}/sign-off", json={})
    assert r.status_code == 409 and "superseded" in r.json()["detail"]


def test_sign_off_records_who_and_when_and_freezes_the_version(owner):
    c, cid = owner["client"], owner["cid"]
    v = c.post(base(c, cid), json={}).json()
    r = c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={"note": "Reviewed"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "signed_off" and out["signed_by"] and out["signed_at"] and out["sign_note"] == "Reviewed"
    assert c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={}).status_code == 409        # cannot be signed twice
    assert c.get("/api/activity/verify" if False else f"/api/companies/{cid}/activity/verify").json()["valid"] is True


def test_open_exceptions_must_be_acknowledged(owner):
    c, cid = owner["client"], owner["cid"]
    add_exception(cid)
    v = c.post(base(c, cid), json={}).json()
    assert v["open_exceptions"] == 1 and v["open_high"] == 1
    refused = c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={})
    assert refused.status_code == 409 and "still open" in refused.json()["detail"]
    ok = c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={"acknowledge_open": True})
    assert ok.status_code == 200


def test_sign_off_is_refused_if_the_audit_work_changed_after_the_snapshot(owner):
    c, cid = owner["client"], owner["cid"]
    v = c.post(base(c, cid), json={}).json()
    add_exception(cid, status="resolved")                          # the work moved on after the snapshot
    r = c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={})
    assert r.status_code == 409 and "changed" in r.json()["detail"]


def test_only_audit_roles_can_create_or_sign_and_other_clients_cannot_see_versions(owner, app_client):
    c, cid = owner["client"], owner["cid"]
    v = c.post(base(c, cid), json={}).json()
    c.post("/api/auth/logout")
    register(app_client, email="other@example.test", org="Other Firm")
    assert app_client.get(f"{base(app_client, cid)}/{v['id']}").status_code == 404
    assert app_client.post(base(app_client, cid), json={}).status_code == 404


def test_a_role_without_audit_permission_cannot_create_or_sign(owner):
    from app.models import Membership
    c, cid = owner["client"], owner["cid"]
    v = c.post(base(c, cid), json={}).json()
    db = SessionLocal()
    try:
        m = db.query(Membership).filter_by(organization_id=owner["org_id"]).first()
        m.role = "accountant"
        db.commit()
    finally:
        db.close()
    assert c.post(base(c, cid), json={}).status_code == 403
    assert c.post(f"{base(c, cid)}/{v['id']}/sign-off", json={}).status_code == 403
    assert c.get(f"{base(c, cid)}/{v['id']}").status_code == 200       # but can read it
