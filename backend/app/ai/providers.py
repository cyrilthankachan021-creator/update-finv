"""AI provider abstraction.

Business logic never imports a vendor SDK directly; it calls `get_provider().complete_json(...)`.
- No provider is configured by default (FINV_AI_PROVIDER=none): the deterministic engine works alone.
- "gemini" is Google Gemini Flash (free tier) through its OpenAI-compatible API; data leaves this machine.
- There is no automatic fallback between providers: switching provider changes where data goes.
- Outputs are untrusted: callers validate them against a schema and local records.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from ..config import settings


class AIUnavailable(RuntimeError):
    pass


class AIResponseError(RuntimeError):
    pass


@dataclass
class AIResult:
    data: dict
    provider: str
    model: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    name: str
    model: str | None

    def complete_json(self, system: str, payload: dict, schema: dict) -> AIResult: ...


class _RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: list[float] = []
        self.lock = threading.Lock()

    def check(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.calls = [t for t in self.calls if now - t < 60]
            if len(self.calls) >= self.per_minute:
                raise AIUnavailable("AI request limit reached; try again in a minute.")
            self.calls.append(now)


_limiter = _RateLimiter(settings.ai_max_calls_per_minute)


class DisabledProvider:
    name = "none"
    model = None

    def complete_json(self, system, payload, schema):
        raise AIUnavailable("AI assistance is not configured. FinV's deterministic checks still run.")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise AIUnavailable("ANTHROPIC_API_KEY is not set.")
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.ai_timeout_s,
                                          max_retries=settings.ai_max_retries)
        self.model = settings.ai_model or "claude-opus-5-5"

    def complete_json(self, system, payload, schema):
        _limiter.check()
        a = self._anthropic
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
        except a.AuthenticationError as exc:
            raise AIUnavailable("The AI provider rejected the configured credentials.") from exc
        except a.RateLimitError as exc:
            raise AIUnavailable("The AI provider is rate limiting requests.") from exc
        except a.APIStatusError as exc:
            raise AIUnavailable(f"The AI provider returned an error (HTTP {exc.status_code}).") from exc
        except a.APIConnectionError as exc:
            raise AIUnavailable("The AI provider could not be reached.") from exc
        if resp.stop_reason == "refusal":
            raise AIResponseError("The AI provider declined the request.")
        if resp.stop_reason == "max_tokens":
            raise AIResponseError("The AI response was cut off.")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        try:
            data = json.loads(text or "")
        except json.JSONDecodeError as exc:
            raise AIResponseError("The AI response was not valid JSON.") from exc
        return AIResult(data, self.name, self.model, resp.usage.input_tokens, resp.usage.output_tokens)


class OpenAIProvider:
    """OpenAI Chat Completions over HTTPS with JSON-schema response format."""
    name = "openai"
    url = "https://api.openai.com/v1"
    extra: dict = {}

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise AIUnavailable("OPENAI_API_KEY is not set.")
        if not settings.ai_model:
            raise AIUnavailable("FINV_AI_MODEL must name the OpenAI model to use.")
        self.key, self.model = settings.openai_api_key, settings.ai_model

    def complete_json(self, system, payload, schema):
        import httpx
        _limiter.check()
        body = {"model": self.model, **self.extra,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "finv_result", "schema": schema, "strict": True}}}
        last = None
        for attempt in range(settings.ai_max_retries + 1):
            try:
                r = httpx.post(f"{self.url}/chat/completions", json=body, timeout=settings.ai_timeout_s,
                               headers={"Authorization": f"Bearer {self.key}"})
            except httpx.HTTPError as exc:
                last = AIUnavailable("The AI provider could not be reached.")
                last.__cause__ = exc
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = AIUnavailable(f"The AI provider returned HTTP {r.status_code}.")
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code in (400, 401, 403) and "api key" in r.text.lower():
                raise AIUnavailable("The AI provider rejected the configured API key.")
            if r.status_code != 200:
                raise AIUnavailable(f"The AI provider returned an error (HTTP {r.status_code}).")
            j = r.json()
            try:
                data = json.loads(j["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise AIResponseError("The AI response was not valid JSON.") from exc
            u = j.get("usage") or {}
            return AIResult(data, self.name, self.model, u.get("prompt_tokens"), u.get("completion_tokens"))
        raise last or AIUnavailable("The AI provider failed.")


class GeminiProvider(OpenAIProvider):
    """Google Gemini Flash (free tier) through Gemini's OpenAI-compatible API."""
    name = "gemini"
    url = "https://generativelanguage.googleapis.com/v1beta/openai"
    extra = {"reasoning_effort": "none", "temperature": 0}  # no hidden "thinking": fast, and it cannot eat the answer

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise AIUnavailable("GEMINI_API_KEY is not set (free key: https://aistudio.google.com/apikey).")
        self.key, self.model = settings.gemini_api_key, settings.ai_model or settings.gemini_model


_override: Provider | None = None


def set_provider_override(p: Provider | None) -> None:
    """Tests inject a fake provider here."""
    global _override
    _override = p


def get_provider() -> Provider:
    if _override is not None:
        return _override
    if settings.ai_provider == "anthropic":
        return AnthropicProvider()
    if settings.ai_provider == "openai":
        return OpenAIProvider()
    if settings.ai_provider == "gemini":
        return GeminiProvider()
    return DisabledProvider()


def provider_status() -> dict:
    """Safe-to-display status. Never includes credentials."""
    p = settings.ai_provider
    configured = p in {"anthropic", "openai", "gemini"}
    key_present = bool({"anthropic": settings.anthropic_api_key, "openai": settings.openai_api_key,
                        "gemini": settings.gemini_api_key}.get(p))
    model = settings.ai_model or {"anthropic": "claude-opus-5-5", "gemini": settings.gemini_model}.get(p)
    return {"provider": p if configured else "none",
            "model": model if configured else None,
            "credentials_present": key_present if configured else False,
            "runs_on_this_machine": False}
