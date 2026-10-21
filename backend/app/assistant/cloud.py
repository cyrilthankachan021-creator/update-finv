"""Cloud chat models with an OpenAI-compatible API: Groq (open-source models on fast LPU hardware, free tier),
Google Gemini Flash (free tier) and xAI Grok. Used only by the chat assistant, and only for companies whose admin
chose "Cloud AI" in Settings, because the chat context contains client data that is sent to the provider."""
from __future__ import annotations

import json

import httpx

from ..config import settings

PROVIDERS = {
    "groq": {"label": "Groq", "url": "https://api.groq.com/openai/v1", "key": lambda: settings.groq_api_key,
             "model": lambda: settings.groq_model, "env": "GROQ_API_KEY", "model_env": "FINV_GROQ_MODEL",
             "signup": "https://console.groq.com/keys"},
    "gemini": {"label": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key": lambda: settings.gemini_api_key, "model": lambda: settings.gemini_model, "env": "GEMINI_API_KEY",
               "model_env": "FINV_GEMINI_MODEL", "signup": "https://aistudio.google.com/apikey",
               "extra": {"reasoning_effort": "none"}},  # no hidden "thinking" that could use up max_tokens
    "xai": {"label": "xAI Grok", "url": "https://api.x.ai/v1", "key": lambda: settings.xai_api_key,
            "model": lambda: settings.xai_model, "env": "XAI_API_KEY", "model_env": "FINV_XAI_MODEL",
            "signup": "https://console.x.ai"},
}


class CloudError(RuntimeError):
    pass


def active() -> dict | None:
    return PROVIDERS.get(settings.chat_provider)


def status() -> dict:
    p = active()
    if not p:
        return {"configured": False}
    return {"configured": bool(p["key"]() and p["model"]()), "label": p["label"], "model": p["model"](),
            "missing": None if p["key"]() else p["env"], "signup": p["signup"]}


def _messages(messages: list[dict]) -> list[dict]:
    """Our loop keeps messages with tool-call arguments as dicts; convert tool-call arguments to the JSON strings OpenAI-style APIs expect."""
    out = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            m["tool_calls"] = [{"id": c["id"], "type": "function",
                                "function": {"name": c["function"]["name"],
                                             "arguments": c["function"]["arguments"] if isinstance(c["function"]["arguments"], str)
                                             else json.dumps(c["function"]["arguments"])},
                                # Gemini 3 signs each tool call and rejects the next turn if the signature is missing
                                **({"extra_content": c["extra_content"]} if c.get("extra_content") else {})}
                               for c in m["tool_calls"]]
        out.append(m)
    return out


def call(messages: list[dict], tools: list[dict], num_predict: int = 300, on_token=None) -> dict:
    p = active()
    key, model = p["key"](), p["model"]()
    if not key:
        raise CloudError(f"{p['label']} needs an API key: add {p['env']}=... to backend/.env (free key: {p['signup']}).")
    if not model:
        raise CloudError(f"Set the {p['label']} model name ({p['model_env']}) in backend/.env.")
    body = {"model": model, "messages": _messages(messages), "tools": tools, "tool_choice": "auto",
            "temperature": 0, "max_tokens": num_predict, "stream": on_token is not None, **p.get("extra", {})}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if on_token is None:
            r = httpx.post(f"{p['url']}/chat/completions", json=body, headers=headers, timeout=60)
            _check(r, p)
            m = r.json()["choices"][0]["message"]
            return {"content": m.get("content") or "", "tool_calls": m.get("tool_calls") or []}
        content, calls, emitted = "", {}, False
        with httpx.stream("POST", f"{p['url']}/chat/completions", json=body, headers=headers, timeout=60) as r:
            if r.status_code != 200:
                r.read()
                _check(r, p)
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                                  "function": {"name": "", "arguments": ""}})
                    slot["id"] = tc.get("id") or slot["id"]
                    if tc.get("extra_content"):
                        slot["extra_content"] = tc["extra_content"]
                    f = tc.get("function") or {}
                    slot["function"]["name"] += f.get("name") or ""
                    slot["function"]["arguments"] += f.get("arguments") or ""
                piece = delta.get("content") or ""
                if piece:
                    content += piece
                    if not calls:
                        on_token(content if not emitted else piece, reset=not emitted)
                        emitted = True
        return {"content": content, "tool_calls": [calls[i] for i in sorted(calls)]}
    except httpx.TimeoutException as exc:
        raise CloudError(f"{p['label']} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise CloudError(f"{p['label']} could not be reached. Check the internet connection.") from exc


def _check(r, p) -> None:
    if r.status_code == 200:
        return
    detail = ""
    try:
        j = r.json()
        j = j[0] if isinstance(j, list) and j else j  # Gemini wraps its error in a list
        detail = (j.get("error") or {}).get("message", "")
    except Exception:
        pass
    if r.status_code == 401 or "api key" in detail.lower():  # Gemini answers a bad key with HTTP 400
        raise CloudError(f"{p['label']} rejected the API key ({p['env']}). Check it at {p['signup']}.")
    if r.status_code == 429:
        raise CloudError(f"{p['label']}'s free-tier limit was reached. Wait a minute and try again.")
    if r.status_code == 404 or "model not found" in detail.lower() or "does not exist" in detail.lower():
        raise CloudError(f"{p['label']} does not offer the model '{p['model']()}'. Set another model name in backend/.env. {detail}".strip())
    raise CloudError(f"{p['label']} returned an error (HTTP {r.status_code}). {detail}".strip())
