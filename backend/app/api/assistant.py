from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..assistant import agent, voice
from ..config import settings
from ..db import get_db
from ..models import AssistantAction, AssistantMessage, utcnow
from ..security import CompanyAccess, company_access
from ..services import accounting_service as acc_svc

router = APIRouter(prefix="/api/companies/{company_id}/assistant", tags=["assistant"])

YES = re.compile(r"^\s*(yes|yeah|yep|confirm|confirmed|ok|okay|go ahead|do it|haan|ha|ji haan)\b[\s.!]*$", re.I)
NO = re.compile(r"^\s*(no|nope|cancel|stop|don'?t|nahi|mat karo)\b[\s.!]*$", re.I)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    workspace: str = Field(pattern=r"^(accounting|audit|client)$")
    page: str = Field(default="", max_length=200)
    # The client this chat is about. The URL already names it and is what authorises access; if a body names a different
    # one, the request is refused rather than guessed at.
    client_id: int | None = None


def _store_reply(db, acc: CompanyAccess, workspace: str, text: str, meta: dict) -> dict:
    m = AssistantMessage(company_id=acc.company.id, user_id=acc.user.id, workspace=workspace, role="assistant",
                         content=text, meta=meta)
    db.add(m)
    db.flush()
    return {"id": m.id, "role": "assistant", "content": text, "meta": meta}


@router.get("/status")
def status(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    ok, why = agent.model_available(db, acc.company)
    from ..assistant import cloud
    full = agent.full_chat_available(db, acc.company)
    st = cloud.status() if full else None
    return {"model": ok, "model_note": why or None, "voice": voice.available(),
            "model_name": (st["model"] if full else settings.gemini_model) if ok else None, "runs_on_this_machine": False,
            "service": st["label"] if full else "Google Gemini (your typed text only)"}


@router.get("/messages")
def messages(workspace: str, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    ms = db.scalars(select(AssistantMessage).where(AssistantMessage.company_id == acc.company.id,
                                                   AssistantMessage.user_id == acc.user.id,
                                                   AssistantMessage.workspace == workspace)
                    .order_by(AssistantMessage.id.desc()).limit(40)).all()[::-1]
    actions = {a.id: agent.action_view(a) for a in db.scalars(select(AssistantAction).where(
        AssistantAction.company_id == acc.company.id, AssistantAction.user_id == acc.user.id))}
    out = []
    for m in ms:
        meta = dict(m.meta or {})
        meta["actions"] = [actions.get(a["id"], a) for a in meta.get("actions", [])]
        out.append({"id": m.id, "role": m.role, "content": m.content, "meta": meta})
    return out


@router.post("/chat")
def chat(body: ChatIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _chat(body, acc, db)


@router.post("/chat/stream")
def chat_stream(body: ChatIn, acc: CompanyAccess = Depends(company_access)):
    """Same as /chat, but streams the reply as newline-delimited JSON events while the model writes it:
    {"type":"delta","text":..,"reset":bool} | {"type":"status","text":..} | {"type":"done","message":{...}}."""
    if body.client_id is not None and body.client_id != acc.company.id:
        raise HTTPException(409, "This message names a different client than the page you are on. Reload and try again.")
    import json as _json
    import queue
    import threading

    from ..db import SessionLocal
    from ..models import Company, User
    q: queue.Queue = queue.Queue()
    company_id, user_id, role = acc.company.id, acc.user.id, acc.role

    def work():
        db = SessionLocal()   # own session: the request's session is closed before streaming ends
        try:
            a = CompanyAccess(db.get(Company, company_id), role, db.get(User, user_id))

            def emit(kind, text, reset=False):
                q.put({"type": kind, "text": text, **({"reset": reset} if kind == "delta" else {})})
            q.put({"type": "done", "message": _chat(body, a, db, emit)})
        except Exception:
            db.rollback()
            q.put({"type": "done", "message": {"id": None, "role": "assistant", "meta": {"error": True},
                                               "content": "Something went wrong. Nothing was changed. Try again."}})
        finally:
            db.close()
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def events():
        while True:
            ev = q.get()
            if ev is None:
                return
            yield _json.dumps(ev, default=str) + "\n"
    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store",
                                                                                  "X-Accel-Buffering": "no"})


def _chat(body: ChatIn, acc: CompanyAccess, db: Session, emit=None) -> dict:
    if body.client_id is not None and body.client_id != acc.company.id:
        raise HTTPException(409, "This message names a different client than the page you are on. Reload and try again.")
    pending = agent.latest_pending(db, acc.company, acc.user.id)
    if pending and (YES.match(body.message) or NO.match(body.message)):
        db.add(AssistantMessage(company_id=acc.company.id, user_id=acc.user.id, workspace=body.workspace,
                                role="user", content=body.message))
        if NO.match(body.message):
            pending.status, pending.decided_at = "cancelled", utcnow()
            res = _store_reply(db, acc, body.workspace, "Cancelled. Nothing was changed.", {"actions": [agent.action_view(pending)]})
        elif (pending.details or {}).get("needs_note") and not (pending.args or {}).get("note"):
            res = _store_reply(db, acc, body.workspace,
                               "This entry has review points, so approval needs a short explanation. Type it in the "
                               "confirmation box and press Confirm.", {"actions": [agent.action_view(pending)]})
        else:
            try:
                text = agent.confirm_action(db, acc.company, acc.user.id, acc.role, pending)
            except acc_svc.WorkflowError as exc:
                text = f"I could not do that: {exc.message}"
            res = _store_reply(db, acc, body.workspace, text, {"actions": [agent.action_view(pending)], "changed": True})
        db.commit()
        return res
    try:
        res = agent.chat(db, acc.company, acc.user.id, acc.role, body.workspace, body.page, body.message, emit)
    except acc_svc.WorkflowError as exc:
        db.rollback()
        db.add(AssistantMessage(company_id=acc.company.id, user_id=acc.user.id, workspace=body.workspace,
                                role="user", content=body.message))
        res = _store_reply(db, acc, body.workspace, exc.message, {"error": True})
    db.commit()
    return res


class ConfirmIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: int, body: ConfirmIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    a = db.get(AssistantAction, action_id)
    if a is None or a.company_id != acc.company.id or a.user_id != acc.user.id:
        raise HTTPException(404, "Action not found.")
    try:
        text = agent.confirm_action(db, acc.company, acc.user.id, acc.role, a, body.note)
    except acc_svc.WorkflowError as exc:
        db.commit()
        raise HTTPException(exc.status, {"message": exc.message, **exc.details} if exc.details else exc.message)
    db.commit()
    return {"message": text, "action": agent.action_view(a)}


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    a = db.get(AssistantAction, action_id)
    if a is None or a.company_id != acc.company.id or a.user_id != acc.user.id:
        raise HTTPException(404, "Action not found.")
    if a.status == "pending":
        a.status, a.decided_at = "cancelled", utcnow()
    db.commit()
    return {"action": agent.action_view(a)}


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), acc: CompanyAccess = Depends(company_access)):
    data = await audio.read(voice.MAX_AUDIO_BYTES + 1)
    try:
        return voice.transcribe(data)
    except voice.VoiceUnavailable as exc:
        raise HTTPException(503 if "not installed" in str(exc) else 422, str(exc))
