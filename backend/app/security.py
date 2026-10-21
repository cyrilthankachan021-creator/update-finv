"""Authentication (local accounts, HttpOnly session cookies) and role-based, company-scoped authorization."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Company, CompanyAssignment, Membership, SessionToken, User, utcnow

COOKIE = "finv_session"
ROLES = ["owner", "admin", "accountant", "reviewer", "auditor", "viewer"]
PERMISSIONS = {
    "view": set(ROLES),
    "prepare": {"owner", "admin", "accountant"},     # upload, correct, propose
    "approve": {"owner", "admin", "reviewer"},
    "post": {"owner", "admin", "reviewer"},
    "audit": {"owner", "admin", "auditor"},
    "admin": {"owner", "admin"},                    # settings, chart, periods, policies, retention
}


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, salt, dk = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    test = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(test.hex(), dk)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(token_hash=_token_hash(token), user_id=user.id,
                        expires_at=utcnow() + timedelta(hours=settings.session_hours)))
    return token


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.query(SessionToken).filter(SessionToken.token_hash == _token_hash(token)).delete()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    st = db.scalar(select(SessionToken).where(SessionToken.token_hash == _token_hash(token)))
    if not st or st.expires_at < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has expired. Sign in again.")
    user = db.get(User, st.user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return user


class CompanyAccess:
    def __init__(self, company: Company, role: str, user: User):
        self.company, self.role, self.user = company, role, user

    def require(self, perm: str) -> None:
        if self.role not in PERMISSIONS[perm]:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Your role ({self.role}) does not allow this action.")


FIRM_WIDE_ROLES = {"owner", "admin"}


def visible_to(db: Session, user_id: int, companies: list, roles: dict[int, str]) -> list:
    """Drop the companies a user is not assigned to when the company is restricted (it has assignments). Owners and
    admins of the firm see everything in it."""
    if not companies:
        return companies
    ids = [c.id for c in companies]
    assigned: dict[int, set[int]] = {}
    for cid, uid in db.execute(select(CompanyAssignment.company_id, CompanyAssignment.user_id).where(CompanyAssignment.company_id.in_(ids))):
        assigned.setdefault(cid, set()).add(uid)
    return [c for c in companies if roles.get(c.organization_id) in FIRM_WIDE_ROLES or c.id not in assigned or user_id in assigned[c.id]]


def company_access(company_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> CompanyAccess:
    """Resolves the company only if the user is a member of its organization. Unknown and foreign companies
    both return 404 so that company ids cannot be probed."""
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
    m = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                           Membership.organization_id == company.organization_id))
    if m is None or not visible_to(db, user.id, [company], {company.organization_id: m.role}):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
    return CompanyAccess(company, m.role, user)


def require_csrf_header(request: Request) -> None:
    """State-changing requests must carry X-Requested-With (not settable cross-site without CORS preflight).
    Combined with SameSite=Strict cookies this blocks cross-site request forgery."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("x-requested-with") != "finv":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing request header.")
