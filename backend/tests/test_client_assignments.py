"""Per-client assignment: unassigned clients stay open to the firm; assigned ones are limited to those people and the firm's owners and admins."""
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Membership, User
from tests.conftest import login, register


def add_member(owner_client, org_id, email, role):
    other = TestClient(owner_client.app)
    other.headers.update({"X-Requested-With": "finv"})
    me = register(other, email=email, name=email.split("@")[0], org=f"{email} Own Firm")
    db = SessionLocal()
    try:
        uid = db.query(User).filter_by(email=email).one().id
        db.add(Membership(user_id=uid, organization_id=org_id, role=role))
        db.commit()
    finally:
        db.close()
    return other, uid


def test_unassigned_clients_are_open_to_the_whole_firm(owner, app_client):
    other, _ = add_member(owner["client"], owner["org_id"], "acct@example.test", "accountant")
    assert owner["cid"] in [c["id"] for c in other.get("/api/companies").json()]
    assert other.get(f"/api/companies/{owner['cid']}/settings").status_code == 200


def test_an_assigned_client_is_hidden_from_other_staff_everywhere(owner, app_client):
    c, cid = owner["client"], owner["cid"]
    staff, staff_id = add_member(c, owner["org_id"], "acct@example.test", "accountant")
    second, _ = add_member(c, owner["org_id"], "second@example.test", "accountant")
    owner_id = c.get("/api/auth/me").json()["id"]
    assert c.put(f"/api/companies/{cid}/assignees", json={"user_ids": [staff_id]}).json() == {"user_ids": [staff_id], "restricted": True}
    assert cid in [x["id"] for x in staff.get("/api/companies").json()]                      # assigned: sees it
    assert cid not in [x["id"] for x in second.get("/api/companies").json()]                 # not assigned: it is not even listed
    assert second.get(f"/api/companies/{cid}/documents").status_code == 404                  # and every route says 404
    assert second.get(f"/api/firm/clients/{cid}").status_code == 404
    assert cid not in [t["client_id"] for t in second.get("/api/firm/tasks").json()["items"]]
    assert second.get("/api/firm/overview").json()["summary"]["clients"] == 0
    assert cid in [x["id"] for x in c.get("/api/companies").json()]                           # the firm's owner still sees all of it
    assert owner_id not in c.get(f"/api/companies/{cid}/assignees").json()["user_ids"]


def test_only_owners_and_admins_change_assignments_and_only_to_firm_members(owner, app_client):
    c, cid = owner["client"], owner["cid"]
    staff, staff_id = add_member(c, owner["org_id"], "acct@example.test", "accountant")
    assert staff.put(f"/api/companies/{cid}/assignees", json={"user_ids": [staff_id]}).status_code == 403
    assert c.put(f"/api/companies/{cid}/assignees", json={"user_ids": [99999]}).status_code == 422
    c.put(f"/api/companies/{cid}/assignees", json={"user_ids": [staff_id]})
    assert c.put(f"/api/companies/{cid}/assignees", json={"user_ids": []}).json()["restricted"] is False      # clearing reopens it


def test_opening_a_client_and_viewing_its_files_is_logged_without_flooding(owner):
    from app.models import AuditEvent
    c, cid = owner["client"], owner["cid"]
    for _ in range(3):
        assert c.get(f"/api/firm/clients/{cid}").status_code == 200
    db = SessionLocal()
    try:
        opened = db.query(AuditEvent).filter_by(company_id=cid, action="client.opened").count()
    finally:
        db.close()
    assert opened == 1                                                      # three refreshes, one visit
    assert c.get(f"/api/companies/{cid}/activity/verify").json()["valid"] is True
