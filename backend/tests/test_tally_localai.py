"""Tally XML export/push (against a fake Tally HTTP server) and AI suggestions (Gemini) behaviour."""
import xml.etree.ElementTree as ET
from decimal import Decimal

import httpx
import pytest

from app.ai import providers
from app.ai.providers import AIResult, GeminiProvider, set_provider_override
from app.config import settings
from tests.conftest import SUPPLIER_MH, upload

INV = f"""Type,Invoice No,Invoice Date,Supplier Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,Total,Category
Purchase,INV-001,10/06/2025,Sharma & Sons <Stationers>,{SUPPLIER_MH},27,1000.00,90.00,90.00,1180.00,Office Expenses
"""
BANK = "Date,Description,Debit,Credit\n05/06/2025,SMS CHARGES QTR 99887766,17.70,\n"


def _posted(c, cid):
    upload(c, cid, "i.csv", INV)
    rid = c.get(f"/api/companies/{cid}/records").json()["items"][0]["id"]
    eid = c.get(f"/api/companies/{cid}/records/{rid}").json()["entries"][0]["id"]
    assert c.post(f"/api/companies/{cid}/entries/{eid}/approve", json={}).status_code == 200
    assert c.post(f"/api/companies/{cid}/entries/{eid}/post").status_code == 200
    return eid


# ---------------------------------------------------------------- Tally
def test_tally_xml_is_balanced_party_wise_and_escaped(owner):
    c, cid = owner["client"], owner["cid"]
    eid = _posted(c, cid)
    st = c.get(f"/api/companies/{cid}/tally/status").json()
    assert [p["id"] for p in st["pending"]] == [eid] and st["pending"][0]["voucher_type"] == "Purchase"
    v = ET.fromstring(c.get(f"/api/companies/{cid}/tally/vouchers.xml").content)
    vch = v.find(".//VOUCHER")
    assert vch.get("VCHTYPE") == "Purchase" and vch.findtext("DATE") == "20250610"
    assert vch.findtext("VOUCHERNUMBER") == "INV-001" and vch.get("REMOTEID") == f"FINV-{cid}-{eid}"
    assert vch.findtext("PARTYLEDGERNAME") == "Sharma & Sons <Stationers>"  # escaped correctly by the XML writer
    lines = vch.findall("ALLLEDGERENTRIES.LIST")
    assert sum(Decimal(l.findtext("AMOUNT")) for l in lines) == 0           # Tally vouchers must net to zero
    debit = [l for l in lines if l.findtext("ISDEEMEDPOSITIVE") == "Yes"]
    assert all(Decimal(l.findtext("AMOUNT")) < 0 for l in debit)            # Tally sign convention
    m = ET.fromstring(c.get(f"/api/companies/{cid}/tally/masters.xml").content)
    groups = {l.get("NAME"): l.findtext("PARENT") for l in m.iter("LEDGER")}
    assert groups["Sharma & Sons <Stationers>"] == "Sundry Creditors"
    assert groups["Input CGST"] == "Duties & Taxes" and groups["Office Expenses"] == "Indirect Expenses"


def test_tally_file_export_marked_once(owner):
    c, cid = owner["client"], owner["cid"]
    eid = _posted(c, cid)
    assert c.post(f"/api/companies/{cid}/tally/mark-exported", json={"entry_ids": [eid]}).status_code == 200
    assert c.get(f"/api/companies/{cid}/tally/status").json()["pending"] == []
    assert c.get(f"/api/companies/{cid}/tally/vouchers.xml").status_code == 404   # nothing left to export
    assert c.get(f"/api/companies/{cid}/tally/vouchers.xml", params={"include_exported": True}).status_code == 200


def test_tally_only_posted_entries_and_roles(owner):
    c, cid = owner["client"], owner["cid"]
    upload(c, cid, "i.csv", INV)
    rid = c.get(f"/api/companies/{cid}/records").json()["items"][0]["id"]
    eid = c.get(f"/api/companies/{cid}/records/{rid}").json()["entries"][0]["id"]
    assert c.get(f"/api/companies/{cid}/tally/status").json()["pending"] == []     # validated, not posted
    assert c.post(f"/api/companies/{cid}/tally/mark-exported", json={"entry_ids": [eid]}).status_code == 409


class FakeTally:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, url, content=None, headers=None, timeout=None):
        self.calls.append((url, content))
        body = content.decode()
        if "<VOUCHER" in body:
            n = body.count("<VOUCHER ")
            resp = (f"<RESPONSE><CREATED>{n}</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>" if self.ok else
                    "<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Ledger 'Office Expenses' does not exist!</LINEERROR></RESPONSE>")
        else:
            resp = f"<RESPONSE><CREATED>{body.count('<LEDGER ')}</CREATED><ERRORS>0</ERRORS></RESPONSE>"
        return httpx.Response(200, text=resp)


def test_tally_push_success_and_idempotent(owner, monkeypatch):
    c, cid = owner["client"], owner["cid"]
    _posted(c, cid)
    fake = FakeTally()
    monkeypatch.setattr(httpx, "post", fake)
    r = c.post(f"/api/companies/{cid}/tally/push", json={"url": "http://127.0.0.1:9000"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["vouchers_result"]["created"] == 1 and len(fake.calls) == 2
    again = c.post(f"/api/companies/{cid}/tally/push", json={}).json()
    assert again["vouchers"] == 0 and len(fake.calls) == 2   # already exported: nothing sent twice
    assert "tally.push" in [a["action"] for a in c.get(f"/api/companies/{cid}/activity").json()]


def test_tally_push_errors_are_reported_and_not_marked(owner, monkeypatch):
    c, cid = owner["client"], owner["cid"]
    _posted(c, cid)
    monkeypatch.setattr(httpx, "post", FakeTally(ok=False))
    r = c.post(f"/api/companies/{cid}/tally/push", json={"url": "http://192.168.1.20:9000"})
    assert r.status_code == 409 and "does not exist" in r.json()["detail"]["message"]
    assert len(c.get(f"/api/companies/{cid}/tally/status").json()["pending"]) == 1

    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "post", down)
    r = c.post(f"/api/companies/{cid}/tally/push", json={})
    assert r.status_code == 503 and "Tally did not answer" in r.json()["detail"]


@pytest.mark.parametrize("url", ["http://8.8.8.8:9000", "http://example.com:9000", "file:///etc/passwd", "ftp://127.0.0.1"])
def test_tally_push_refuses_non_local_addresses(owner, url):
    c, cid = owner["client"], owner["cid"]
    _posted(c, cid)
    assert c.post(f"/api/companies/{cid}/tally/push", json={"url": url}).status_code == 422


# ---------------------------------------------------------------- AI suggestions (Gemini)
class GeminiFake:
    name, model = "gemini", "gemini-3.5-flash"

    def __init__(self, data):
        self.data, self.calls = data, []

    def complete_json(self, system, payload, schema):
        self.calls.append((payload, schema))
        return AIResult(self.data, self.name, self.model, 5, 5)


GOOD = {"record_ref": "TXN_1", "account_code": "5500", "category": "Bank charges", "confidence": 0.9,
        "rationale": "SMS charges", "evidence_fields": ["description"]}


def _bank(c, cid):
    upload(c, cid, "b.csv", BANK, kind="bank_statement")
    return c.get(f"/api/companies/{cid}/records").json()["items"][0]["id"]


def test_suggestion_needs_consent_and_sends_minimised_data(owner):
    c, cid = owner["client"], owner["cid"]
    rid = _bank(c, cid)
    fake = GeminiFake(GOOD)
    set_provider_override(fake)
    for mode in ("local_only", "local_ai"):   # AI off, and the retired "this machine only" mode refuses the cloud
        c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": mode})
        assert c.post(f"/api/companies/{cid}/records/{rid}/ai-suggest").status_code == 403
    assert fake.calls == []
    assert c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "pseudonymized_ai"}).status_code == 200
    r = c.post(f"/api/companies/{cid}/records/{rid}/ai-suggest")
    assert r.status_code == 200 and r.json()["origin"] == "ai_assisted" and r.json()["status"] == "needs_review"
    payload, schema = fake.calls[0]
    assert "99887766" not in str(payload)  # account numbers never leave the machine
    assert set(schema["properties"]["account_code"]["anyOf"][0]["enum"]) >= {"5500"}  # constrained to real ledgers


def test_string_null_from_model_means_no_suggestion(owner):
    c, cid = owner["client"], owner["cid"]
    rid = _bank(c, cid)
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "pseudonymized_ai"})
    set_provider_override(GeminiFake({**GOOD, "account_code": "null"}))
    r = c.post(f"/api/companies/{cid}/records/{rid}/ai-suggest")
    assert r.status_code == 422 and "could not suggest" in r.json()["detail"]


def test_connection_test_uses_fictional_data(owner):
    c, cid = owner["client"], owner["cid"]
    _bank(c, cid)
    fake = GeminiFake(GOOD)
    set_provider_override(fake)
    r = c.post(f"/api/companies/{cid}/ai/test").json()
    assert r["ok"] and r["sensible"] and r["runs_on_this_machine"] is False
    assert "99887766" not in str(fake.calls[0][0]) and fake.calls[0][0]["transaction"]["amount"] == "17.70"
    set_provider_override(GeminiFake({**GOOD, "account_code": "4100"}))
    assert c.post(f"/api/companies/{cid}/ai/test").json()["sensible"] is False
    set_provider_override(GeminiFake({**GOOD, "account_code": "7777"}))
    bad = c.post(f"/api/companies/{cid}/ai/test").json()
    assert bad["ok"] is False and bad["stage"] == "response"
    set_provider_override(None)
    none = c.post(f"/api/companies/{cid}/ai/test").json()
    assert none["ok"] is False and "not configured" in none["message"]


def test_gemini_provider_request_and_errors(monkeypatch):
    monkeypatch.setattr(settings, "ai_model", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(providers.AIUnavailable, match="GEMINI_API_KEY"):
        GeminiProvider()
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    p = GeminiProvider()
    assert p.model == "gemini-3.5-flash"
    seen = {}

    def ok(url, json=None, timeout=None, headers=None):
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 4}})
    monkeypatch.setattr(httpx, "post", ok)
    providers._limiter.calls.clear()
    r = p.complete_json("sys", {"x": 1}, {"type": "object"})
    assert r.data == {"a": 1} and r.input_tokens == 3
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer g-key" and seen["body"]["reasoning_effort"] == "none"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == {"type": "object"}
    bad_key = [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(400, json=bad_key))
    with pytest.raises(providers.AIUnavailable, match="rejected the configured API key"):
        p.complete_json("sys", {}, {})
