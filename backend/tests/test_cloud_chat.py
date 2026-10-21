"""Groq / Gemini / xAI cloud chat (simulated API), the cloud privacy gate, and the streaming chat endpoint."""
import json

import httpx
import pytest

from app.assistant import agent, cloud
from app.config import settings
from tests.conftest import upload

BANK = "Date,Description,Debit,Credit\n05/06/2025,SMS CHARGES QTR,17.70,\n"


def say(c, cid, text):
    return c.post(f"/api/companies/{cid}/assistant/chat", json={"message": text, "workspace": "accounting", "page": "/"}).json()


@pytest.fixture()
def groq(monkeypatch, owner):
    monkeypatch.setattr(settings, "chat_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    sent = []
    replies = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "body": json, "headers": headers})
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0)}]})
    monkeypatch.setattr(httpx, "post", post)
    return owner, sent, replies


def test_cloud_chat_needs_explicit_company_consent(groq):
    owner, sent, _ = groq
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "local_ai"})
    r = say(c, cid, "tell me something interesting about ledgers")
    assert "has not allowed" not in r["content"] and sent == []   # nothing sent, and no "ask an admin" message
    st = c.get(f"/api/companies/{cid}/assistant/status").json()
    assert st["model"] is False   # no Gemini key in this test, so only built-in commands


def test_groq_tool_loop_with_openai_format(groq):
    owner, sent, replies = groq
    c, cid = owner["client"], owner["cid"]
    upload(c, cid, "b.csv", BANK, kind="bank_statement")
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    replies += [
        {"content": None, "tool_calls": [{"id": "call_abc", "type": "function",
                                          "function": {"name": "list_ledgers", "arguments": '{"search": "bank"}'}}]},
        {"content": "Bank Charges is ledger 5500."},
    ]
    r = say(c, cid, "suggest a suitable ledger for courier fees")
    assert r["content"] == "Bank Charges is ledger 5500." and r["meta"]["tools"] == ["list_ledgers"]
    first, second = sent
    assert first["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert first["headers"]["Authorization"] == "Bearer test-key" and first["body"]["model"] == settings.groq_model
    assert [t["function"]["name"] for t in first["body"]["tools"]] == agent.MODEL_TOOLS["accounting"]
    tool_msg = second["body"]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_abc" and "Bank Charges" in tool_msg["content"]
    assert isinstance(second["body"]["messages"][-2]["tool_calls"][0]["function"]["arguments"], str)
    st = c.get(f"/api/companies/{cid}/assistant/status").json()
    assert st["model"] and st["service"] == "Groq" and st["runs_on_this_machine"] is False


def test_groq_errors_are_plain(groq, monkeypatch):
    owner, _, _ = groq
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    assert "rejected the API key" in say(c, cid, "tell me something interesting about ledgers")["content"]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, json={"error": {"message": "rate limit"}}))
    assert "free-tier limit" in say(c, cid, "tell me something interesting about ledgers")["content"]
    monkeypatch.setattr(settings, "groq_api_key", None)
    r = say(c, cid, "tell me something interesting about ledgers")
    assert "GROQ_API_KEY" in r["content"] and "console.groq.com" in r["content"]


def test_streaming_endpoint_sends_words_then_final_message(groq, monkeypatch):
    owner, _, _ = groq
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    sse = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': w}}]})}\n\n" for w in ["A credit ", "note reduces ", "the amount due."])
    sse += "data: [DONE]\n\n"

    class FakeStream:
        def __init__(self, *a, **k):
            self.status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def iter_lines(self):
            return iter(sse.splitlines())
    monkeypatch.setattr(httpx, "stream", FakeStream)
    r = c.post(f"/api/companies/{cid}/assistant/chat/stream",
               json={"message": "what does a credit note do, briefly", "workspace": "accounting", "page": "/"})
    events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    deltas = [e for e in events if e["type"] == "delta"]
    assert len(deltas) == 3 and deltas[0]["reset"] and deltas[0]["text"] == "A credit "
    assert events[-1]["type"] == "done" and events[-1]["message"]["content"] == "A credit note reduces the amount due."
    hist = c.get(f"/api/companies/{cid}/assistant/messages", params={"workspace": "accounting"}).json()
    assert hist[-1]["content"] == "A credit note reduces the amount due."


def test_instant_answers_never_reach_the_cloud(groq):
    owner, sent, _ = groq
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    assert say(c, cid, "hello")["content"].startswith("Hello") and sent == []


def test_gemini_uses_its_openai_endpoint_and_plain_errors(groq, monkeypatch):
    owner, sent, replies = groq
    monkeypatch.setattr(settings, "chat_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    replies.append({"content": "Ledgers group your accounts."})
    assert say(c, cid, "tell me something interesting about ledgers")["content"] == "Ledgers group your accounts."
    assert sent[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert sent[0]["headers"]["Authorization"] == "Bearer g-key" and sent[0]["body"]["model"] == "gemini-3.5-flash"
    bad_key = [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(400, json=bad_key))
    assert "rejected the API key (GEMINI_API_KEY)" in say(c, cid, "tell me something interesting about ledgers")["content"]
    monkeypatch.setattr(settings, "gemini_api_key", None)
    r = say(c, cid, "tell me something interesting about ledgers")
    assert "GEMINI_API_KEY" in r["content"] and "aistudio.google.com" in r["content"]


def test_gemini_tool_call_signature_is_sent_back(groq, monkeypatch):
    owner, sent, replies = groq
    monkeypatch.setattr(settings, "chat_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    c, cid = owner["client"], owner["cid"]
    c.patch(f"/api/companies/{cid}/settings", json={"privacy_mode": "cloud_ai"})
    sig = {"google": {"thought_signature": "abc123"}}
    replies += [
        {"content": None, "tool_calls": [{"id": "call_1", "type": "function", "extra_content": sig,
                                          "function": {"name": "list_ledgers", "arguments": '{"search": "bank"}'}}]},
        {"content": "Bank Charges is ledger 5500."},
    ]
    assert say(c, cid, "suggest a suitable ledger for courier fees")["content"] == "Bank Charges is ledger 5500."
    assert sent[0]["body"]["reasoning_effort"] == "none"
    assert sent[1]["body"]["messages"][-2]["tool_calls"][0]["extra_content"] == sig
    # Google's error text mentions "model" without the model being the problem
    msg = "Function call is missing a thought_signature; this may lead to degraded model performance."
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(400, json=[{"error": {"message": msg}}]))
    r = say(c, cid, "tell me something interesting about ledgers")["content"]
    assert "does not offer the model" not in r and "thought_signature" in r


def test_owner_can_make_cloud_ai_the_default_for_new_companies(owner, monkeypatch):
    c, org = owner["client"], owner["org_id"]
    assert c.get(f"/api/companies/{owner['cid']}/settings").json()["privacy_mode"] == "local_only"   # default: AI off
    monkeypatch.setattr(settings, "default_privacy_mode", "cloud_ai")
    cid = c.post("/api/companies", json={"organization_id": org, "name": "New Co"}).json()["id"]
    assert c.get(f"/api/companies/{cid}/settings").json()["privacy_mode"] == "cloud_ai"
    monkeypatch.setattr(settings, "default_privacy_mode", "anything-else")   # a typo never turns AI on
    cid = c.post("/api/companies", json={"organization_id": org, "name": "Other Co"}).json()["id"]
    assert c.get(f"/api/companies/{cid}/settings").json()["privacy_mode"] == "local_only"
