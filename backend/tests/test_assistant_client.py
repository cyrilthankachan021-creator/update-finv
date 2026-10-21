"""The assistant on the client page: scoped to one client, prepares but never files, and says what it did."""
from datetime import date

from app.assistant import agent
from app.models import AuditEvent, GstReturn
from tests.conftest import login, register, upload

BANK = "Date,Description,Reference,Debit,Credit\n05/06/2025,UPI/RENT/ravi@okaxis/Sharma Supplies 9876543210,UTR77,25000.00,\n"


def say(c, cid, text, **kw):
    r = c.post(f"/api/companies/{cid}/assistant/chat", json={"message": text, "workspace": "client", "client_id": cid, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(c, cid, reply):
    a = reply["meta"]["actions"][0]
    r = c.post(f"/api/companies/{cid}/assistant/actions/{a['id']}/confirm", json={})
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_period_picks_the_latest_such_month_and_never_guesses():
    today = date(2026, 10, 21)
    assert agent.parse_period("September", today) == "2026-09"
    assert agent.parse_period("december", today) == "2025-12"
    assert agent.parse_period("sep 2025", today) == "2025-09"
    assert agent.parse_period("2025-04", today) == "2025-04"
    assert agent.parse_period("last month", today) == "2026-09"
    assert agent.parse_period("whenever", today) is None


def test_prepare_gst_return_waits_for_confirm_then_prepares_but_does_not_approve(owner):
    c, cid = owner["client"], owner["cid"]
    r = say(c, cid, "prepare GSTR-1 for September")
    assert "Press Confirm" in r["content"] and "not approved or filed" in r["content"]
    assert r["meta"]["actions"][0]["status"] == "pending"
    with_session = c.get(f"/api/companies/{cid}/gst/returns").json()
    assert with_session == []                                   # nothing exists until the user confirms
    done = confirm(c, cid, r)
    assert "prepared" in done["message"] and "not approved or filed" in done["message"]
    rets = c.get(f"/api/companies/{cid}/gst/returns").json()
    assert len(rets) == 1 and rets[0]["state"] == "prepared" and rets[0]["return_type"] == "GSTR1"
    assert rets[0].get("approved_at") in (None, "")


def test_asks_one_question_when_the_month_is_missing(owner):
    r = say(owner["client"], owner["cid"], "prepare GSTR-3B")
    assert "Which month" in r["content"] and r["meta"]["actions"] == []


def test_refuses_filing_paying_signing_and_secrets_and_makes_no_change(owner):
    c, cid = owner["client"], owner["cid"]
    for text in ["file my GST return", "submit GSTR-3B to the portal", "pay my GST challan", "approve the gst return",
                 "sign the return with my DSC", "enter the OTP for me", "what is my gst portal password"]:
        r = say(c, cid, text)
        assert "I can't do that" in r["content"], text
        assert r["meta"]["actions"] == [] and r["meta"]["navigate"] is None, text
    assert c.get(f"/api/companies/{cid}/gst/returns").json() == []


def test_entry_commands_are_not_mistaken_for_filing(owner):
    r = say(owner["client"], owner["cid"], "approve entry 12 for tax invoice")
    assert "I can't do that" not in r["content"]            # it is handled as an entry command (entry 12 does not exist)


def test_open_bank_reconciliation_navigates_and_reports_the_step(owner):
    r = say(owner["client"], owner["cid"], "open bank reconciliation")
    assert r["meta"]["navigate"] == "/accounting/bank"
    assert r["meta"]["steps"] == [{"tool": "open_page", "label": "Opened a page", "ok": True}]


def test_tally_sync_is_declined_honestly(owner):
    r = say(owner["client"], owner["cid"], "sync my Tally data")
    assert "haven't imported or synced anything" in r["content"]


def test_list_tasks_and_explain_use_this_clients_data_only(owner):
    c, a = owner["client"], owner["cid"]
    other = c.post("/api/companies", json={"organization_id": owner["org_id"], "name": "Second Client Ltd"}).json()["id"]
    upload(c, other, "bank.csv", BANK, kind="bank_statement")
    r = say(c, a, "what is due?")
    assert "GSTR" in r["content"] and "Second Client" not in r["content"]
    assert "TDS" in r["content"]                               # says what it does not track
    miss = say(c, a, "explain finding 999")
    assert "no finding #999" in miss["content"]


def test_cannot_read_or_act_on_another_clients_data(owner):
    """Same firm: a finding of client B is invisible when chatting as client A. Other firm: the chat itself is a 404."""
    c, a = owner["client"], owner["cid"]
    b = c.post("/api/companies", json={"organization_id": owner["org_id"], "name": "Second Client Ltd"}).json()["id"]
    assert say(c, b, "what is due?")["content"]
    assert c.post(f"/api/companies/{a}/assistant/chat", json={"message": "hi", "workspace": "client", "client_id": b}).status_code == 409
    assert c.post(f"/api/companies/{a}/assistant/chat/stream", json={"message": "hi", "workspace": "client", "client_id": b}).status_code == 409
    action = say(c, a, "prepare GSTR-1 for September")["meta"]["actions"][0]
    assert c.post(f"/api/companies/{b}/assistant/actions/{action['id']}/confirm", json={}).status_code == 404
    c.post("/api/auth/logout")
    register(c, email="other@example.test", org="Other CA")
    assert c.post(f"/api/companies/{a}/assistant/chat", json={"message": "what is due?", "workspace": "client", "client_id": a}).status_code == 404
    assert c.get(f"/api/companies/{a}/assistant/messages?workspace=client").status_code == 404


def test_assistant_actions_are_in_the_audit_trail(owner):
    c, cid = owner["client"], owner["cid"]
    confirm(c, cid, say(c, cid, "prepare GSTR-1 for September"))
    actions = [e["action"] for e in c.get(f"/api/companies/{cid}/activity").json()]
    assert "assistant.command" in actions and "assistant.action_confirmed" in actions and "gst.prepared" in actions
    assert c.get(f"/api/companies/{cid}/activity/verify").json()["valid"]


def test_identifiers_are_masked_before_anything_goes_to_the_model():
    text = agent.mask_ids('{"gstin": "27AAACF1234K1ZX", "pan": "AAACF1234K", "n": 12}')
    assert "27AAACF1234K1ZX" not in text and "AAACF1234K" not in text and '"n": 12' in text
    assert "27XXXXXXXXXXXXX" in text
