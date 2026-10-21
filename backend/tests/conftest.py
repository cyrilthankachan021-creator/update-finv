"""Test fixtures. All data is fictional."""
from __future__ import annotations

import os

os.environ["FINV_NO_ENV_FILE"] = "1"   # never read the developer's backend/.env during tests

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine.gst import gstin_check_digit


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    base = f"{state}{pan}{entity}Z"
    return base + gstin_check_digit(base)


# Fictional, structurally valid GSTINs
COMPANY_GSTIN = make_gstin("27", "AAACF1234K")      # Maharashtra
SUPPLIER_MH = make_gstin("27", "BBBCS5678L")         # same state
SUPPLIER_KA = make_gstin("29", "CCCCT9012M")         # Karnataka
CUSTOMER_MH = make_gstin("27", "DDDDC3456N")


@pytest.fixture(autouse=True)
def no_real_ai_keys(monkeypatch):
    """Tests never reach a real AI service, even when backend/.env holds real keys."""
    for key in ("gemini_api_key", "groq_api_key", "xai_api_key", "anthropic_api_key", "openai_api_key",
                "telegram_bot_token", "telegram_chat_id"):
        monkeypatch.setattr(settings, key, None)
    monkeypatch.setattr(settings, "default_privacy_mode", "local_only")
    monkeypatch.setattr(settings, "gst_open_browser", False)   # tests never open browser windows


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "storage_key", None)
    from app.ai.providers import set_provider_override
    set_provider_override(None)
    from app.main import create_app
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}", create_tables=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Requested-With": "finv"})
        yield c
    set_provider_override(None)


def register(c: TestClient, email="owner@example.test", name="Asha Owner", org="Fictional & Co CA") -> dict:
    r = c.post("/api/auth/register", json={"name": name, "email": email, "password": "correct-horse-battery",
                                           "organization_name": org})
    assert r.status_code == 201, r.text
    return r.json()


def login(c: TestClient, email: str) -> None:
    r = c.post("/api/auth/login", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text


@pytest.fixture()
def owner(app_client):
    me = register(app_client)
    org_id = me["organizations"][0]["id"]
    r = app_client.post("/api/companies", json={"organization_id": org_id, "name": "Sample Traders Pvt Ltd",
                                                "gstin": COMPANY_GSTIN})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    p = app_client.post(f"/api/companies/{cid}/periods", json={"name": "FY 2025-26", "start_date": "2025-04-01",
                                                               "end_date": "2026-03-31"})
    assert p.status_code == 201, p.text
    return {"client": app_client, "me": me, "org_id": org_id, "company": r.json(), "cid": r.json()["id"]}


def upload(c: TestClient, cid: int, name: str, content: bytes | str, kind="auto", workspace="accounting", **form):
    if isinstance(content, str):
        content = content.encode()
    headers = {}
    if "idempotency_key" in form:
        headers["Idempotency-Key"] = form.pop("idempotency_key")
    return c.post(f"/api/companies/{cid}/documents", files={"file": (name, content)},
                  data={"kind": kind, "workspace": workspace, **form}, headers=headers)


@pytest.fixture(autouse=True)
def _fresh_firm_cache():
    from app.api import firm
    firm._cache.clear()
    yield
    firm._cache.clear()


@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    caplog.set_level(logging.INFO)
    yield
