"""Approval notifications over a messaging channel (Telegram today; WhatsApp can be added as another Sender).

Messages carry the minimum: a client reference number, the last 4 characters of the GSTIN, return type, period,
the tax amount rounded to the nearest thousand, the warning count and a single-use review link. No names, invoice
data, full GSTINs or secrets. `check_message` refuses to send anything that breaks this, and no AI is involved.
"""
from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal

import httpx

from ..config import settings

log = logging.getLogger("finv.notify")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
_SECRET_WORDS = re.compile(r"\b(password|otp|evc|dsc|pin|captcha)\b", re.I)


class NotifyError(RuntimeError):
    pass


class MessageNotAllowed(NotifyError):
    """The message would expose client data; it was not sent."""


def rounded_inr(amount: Decimal) -> str:
    k = (amount / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"about ₹{k:,}k" if k else "under ₹500"


def approval_message(company_id: int, gstin: str, return_type: str, period: str, tax: Decimal,
                     blocking: int, info: int, link: str | None) -> str:
    rt = {"GSTR1": "GSTR-1", "GSTR3B": "GSTR-3B"}[return_type]
    lines = [f"FinV: {rt} for {period} is ready for review.",
             f"Client #{company_id}, GSTIN ending {gstin[-4:]}.",
             f"Tax: {rounded_inr(tax)}. Warnings: {blocking} to resolve, {info} for information."]
    lines.append(f"Review and approve in FinV (sign-in required, link works once, expires in "
                 f"{settings.review_link_minutes} min): {link}" if link else "Review and approve it in FinV.")
    return "\n".join(lines)


def status_message(company_id: int, gstin: str, return_type: str, period: str, text: str) -> str:
    rt = {"GSTR1": "GSTR-1", "GSTR3B": "GSTR-3B"}[return_type]
    return f"FinV: {rt} {period}, client #{company_id} (GSTIN ending {gstin[-4:]}): {text}"


def check_message(text: str, forbidden: list[str]) -> None:
    """Refuse full GSTINs, names and anything that looks like a credential."""
    if _GSTIN.search(text):
        raise MessageNotAllowed("the message contains a full GSTIN")
    if _SECRET_WORDS.search(text):
        raise MessageNotAllowed("the message mentions a credential")
    low = text.lower()
    for f in forbidden:
        if f and len(f.strip()) >= 3 and f.strip().lower() in low:
            raise MessageNotAllowed("the message contains a client or party name")


class TelegramSender:
    channel = "telegram"

    def configured(self) -> bool:
        return bool(settings.telegram_bot_token and settings.telegram_chat_id)

    def send(self, text: str) -> None:
        try:
            r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage", timeout=10,
                           json={"chat_id": settings.telegram_chat_id, "text": text,
                                 "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            log.warning("Telegram could not be reached: %s", type(exc).__name__)   # never log the URL: it has the token
            raise NotifyError("Telegram could not be reached.") from exc
        if r.status_code != 200:
            log.warning("Telegram returned HTTP %s", r.status_code)
            raise NotifyError(f"Telegram returned HTTP {r.status_code}. Check the bot token and chat id.")


_sender_override = None


def sender():
    return _sender_override or TelegramSender()


def set_sender_override(s) -> None:
    """Tests use a fake sender."""
    global _sender_override
    _sender_override = s


def send(text: str, forbidden: list[str]) -> str:
    """Returns the channel used, or 'not_configured'. Raises MessageNotAllowed / NotifyError."""
    check_message(text, forbidden)
    s = sender()
    if not s.configured():
        return "not_configured"
    s.send(text)
    return s.channel
