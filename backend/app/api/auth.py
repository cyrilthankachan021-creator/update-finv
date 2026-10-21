from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Membership, Organization, User
from ..security import COOKIE, create_session, current_user, end_session, hash_password, verify_password
from ..services.audit_log import log_event

router = APIRouter(prefix="/api/auth", tags=["auth"])
_failures: dict[str, list[float]] = {}


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
                    max_age=settings.session_hours * 3600, path="/")


def _me(db: Session, user: User) -> dict:
    ms = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    orgs = [{"id": m.organization_id, "name": db.get(Organization, m.organization_id).name, "role": m.role} for m in ms]
    return {"id": user.id, "name": user.name, "email": user.email, "organizations": orgs}


@router.post("/register", status_code=201)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    org = Organization(name=body.organization_name.strip())
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))
    log_event(db, None, user.id, "user.registered", "organization", org.id, {})
    token = create_session(db, user)
    db.commit()
    _set_cookie(response, token)
    return _me(db, user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else '-'}:{body.email.lower()}"
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many failed sign-in attempts. Wait a few minutes and try again.")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        _failures[key] = recent + [now]
        raise HTTPException(401, "Email or password is incorrect.")
    _failures.pop(key, None)
    token = create_session(db, user)
    db.commit()
    _set_cookie(response, token)
    return _me(db, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    end_session(db, request.cookies.get(COOKIE))
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _me(db, user)
