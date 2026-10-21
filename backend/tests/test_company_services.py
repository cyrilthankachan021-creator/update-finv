"""Which work the firm does for each client company (Accounting, Audit or both)."""
from tests.conftest import login, register


def test_services_default_create_and_change(owner):
    c, org, cid = owner["client"], owner["org_id"], owner["cid"]
    assert owner["company"]["services"] == ["accounting", "audit"]               # default: both
    r = c.post("/api/companies", json={"organization_id": org, "name": "Books Only Pvt Ltd", "services": ["accounting"]})
    assert r.status_code == 201 and r.json()["services"] == ["accounting"]
    assert {x["name"]: x["services"] for x in c.get("/api/companies").json()}["Books Only Pvt Ltd"] == ["accounting"]
    assert c.post("/api/companies", json={"organization_id": org, "name": "X", "services": []}).status_code == 422
    assert c.post("/api/companies", json={"organization_id": org, "name": "X", "services": ["tax"]}).status_code == 422

    r = c.put(f"/api/companies/{cid}/services", json={"services": ["audit", "accounting"]})
    assert r.json()["services"] == ["accounting", "audit"]                         # normalised order, no change logged
    r = c.put(f"/api/companies/{cid}/services", json={"services": ["audit"]})
    assert r.status_code == 200 and r.json()["services"] == ["audit"]
    assert c.put(f"/api/companies/{cid}/services", json={"services": []}).status_code == 422
    changes = [a for a in c.get(f"/api/companies/{cid}/activity").json() if a["action"] == "settings.changed"]
    assert len(changes) == 1 and changes[0]["details"]["services"] == {"from": ["accounting", "audit"], "to": ["audit"]}


def test_only_admins_change_services(owner):
    c, org, cid = owner["client"], owner["org_id"], owner["cid"]
    c.post("/api/auth/logout")
    register(c, email="acct@example.test", org="Own")
    login(c, "owner@example.test")
    c.post(f"/api/organizations/{org}/members", json={"email": "acct@example.test", "role": "accountant"})
    c.post("/api/auth/logout")
    login(c, "acct@example.test")
    assert c.put(f"/api/companies/{cid}/services", json={"services": ["audit"]}).status_code == 403
    assert c.post("/api/companies", json={"organization_id": org, "name": "Y", "services": ["audit"]}).status_code == 403
