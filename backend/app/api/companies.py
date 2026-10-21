from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..ai.providers import provider_status
from ..db import get_db
from ..engine.gst import validate_gstin
from ..models import (Account, AccountingPeriod, AIRequestLog, Company, CompanyAssignment, CompanySettings, Membership, Organization,
                      Policy, User, utcnow)
from ..security import ROLES, CompanyAccess, company_access, current_user, visible_to
from ..services import documents_service
from ..services.audit_log import log_event
from ..services.defaults import seed_company
from . import serializers as S

router = APIRouter(prefix="/api", tags=["companies"])


class CompanyIn(BaseModel):
    organization_id: int
    name: str = Field(min_length=1, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str | None = Field(default=None, pattern=r"^\d{2}$")
    services: list[Literal["accounting", "audit"]] = Field(default=["accounting", "audit"], min_length=1, max_length=2)
    gst_filing: Literal["monthly", "qrmp"] = "monthly"
    tds_applicable: bool = False
    tan: str | None = Field(default=None, pattern=r"^[A-Za-z]{4}[0-9]{5}[A-Za-z]$")


class ServicesIn(BaseModel):
    services: list[Literal["accounting", "audit"]] = Field(min_length=1, max_length=2)


def _services(v: list[str]) -> list[str]:
    return [x for x in ("accounting", "audit") if x in v]


@router.get("/companies")
def list_companies(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ms = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    roles = {m.organization_id: m.role for m in ms}
    cs = db.scalars(select(Company).where(Company.organization_id.in_(roles)).order_by(Company.name)).all() if roles else []
    return [S.company(c, roles[c.organization_id]) for c in visible_to(db, user.id, list(cs), roles)]


class AssigneesIn(BaseModel):
    user_ids: list[int] = Field(default_factory=list, max_length=200)


@router.get("/companies/{company_id}/assignees")
def get_assignees(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Who is assigned to this client. An empty list means every member of the firm can open it."""
    ids = db.scalars(select(CompanyAssignment.user_id).where(CompanyAssignment.company_id == acc.company.id)).all()
    return {"user_ids": sorted(ids), "restricted": bool(ids)}


@router.put("/companies/{company_id}/assignees")
def set_assignees(body: AssigneesIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    members = set(db.scalars(select(Membership.user_id).where(Membership.organization_id == acc.company.organization_id)).all())
    wanted = set(body.user_ids)
    if not wanted <= members:
        raise HTTPException(422, "Only members of this firm can be assigned.")
    current = set(db.scalars(select(CompanyAssignment.user_id).where(CompanyAssignment.company_id == acc.company.id)).all())
    for uid in current - wanted:
        db.execute(delete(CompanyAssignment).where(CompanyAssignment.company_id == acc.company.id, CompanyAssignment.user_id == uid))
    for uid in wanted - current:
        db.add(CompanyAssignment(company_id=acc.company.id, user_id=uid))
    log_event(db, acc.company.id, acc.user.id, "company.assignees_set", "company", acc.company.id, {"user_ids": sorted(wanted)})
    db.commit()
    from . import firm
    firm._cache.clear()                  # a changed assignment must take effect at once, not after the cache expires
    return {"user_ids": sorted(wanted), "restricted": bool(wanted)}


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == body.organization_id))
    if not m:
        raise HTTPException(404, "Organization not found.")
    if m.role not in {"owner", "admin"}:
        raise HTTPException(403, "Only owners and admins can add companies.")
    gstin = (body.gstin or "").strip().upper() or None
    if gstin:
        ok, msg = validate_gstin(gstin)
        if not ok:
            raise HTTPException(422, msg)
    name = body.name.strip()
    twin = db.scalar(select(Company).where(Company.organization_id == body.organization_id,
                                           or_(func.lower(Company.name) == name.lower(), (Company.gstin == gstin) if gstin else False)))
    if twin:
        raise HTTPException(409, f"Your firm already has a client called '{twin.name}'" + (" with this GSTIN." if gstin and twin.gstin == gstin else "."))
    c = Company(organization_id=body.organization_id, name=name, gstin=gstin,
                state_code=body.state_code or (gstin[:2] if gstin else None), services=_services(body.services))
    db.add(c)
    db.flush()
    seed_company(db, c.id)
    db.flush()
    st = db.get(CompanySettings, c.id)
    st.gst_filing, st.tds_applicable, st.tan = body.gst_filing, body.tds_applicable, (body.tan or "").upper() or None
    log_event(db, c.id, user.id, "company.created", "company", c.id, {"name": c.name, "services": c.services})
    db.commit()
    return S.company(c, m.role)


@router.get("/companies/{company_id}")
def get_company(acc: CompanyAccess = Depends(company_access)):
    return S.company(acc.company, acc.role)


@router.put("/companies/{company_id}/services")
def set_services(body: ServicesIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Which work the firm does for this client. Turning a service off hides its workspace; no data is deleted."""
    acc.require("admin")
    new = _services(body.services)
    old = acc.company.services or ["accounting", "audit"]
    if new != old:
        acc.company.services = new
        log_event(db, acc.company.id, acc.user.id, "settings.changed", "company", acc.company.id,
                  {"services": {"from": old, "to": new}})
        db.commit()
    return S.company(acc.company, acc.role)


# ---------------------------------------------------------------- members
class MemberIn(BaseModel):
    email: str
    role: str


@router.get("/organizations/{org_id}/members")
def list_members(org_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == org_id)):
        raise HTTPException(404, "Organization not found.")
    ms = db.scalars(select(Membership).where(Membership.organization_id == org_id)).all()
    return [{"user_id": m.user_id, "name": m.user.name, "email": m.user.email, "role": m.role} for m in ms]


@router.post("/organizations/{org_id}/members", status_code=201)
def add_member(org_id: int, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == org_id))
    if not me:
        raise HTTPException(404, "Organization not found.")
    if me.role not in {"owner", "admin"}:
        raise HTTPException(403, "Only owners and admins can manage members.")
    if body.role not in ROLES or (body.role == "owner" and me.role != "owner"):
        raise HTTPException(422, "Invalid role.")
    target = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not target:
        raise HTTPException(404, "No FinV account exists for that email. Ask the person to create an account first.")
    existing = db.scalar(select(Membership).where(Membership.user_id == target.id, Membership.organization_id == org_id))
    if existing:
        existing.role = body.role
    else:
        db.add(Membership(user_id=target.id, organization_id=org_id, role=body.role))
    log_event(db, None, user.id, "organization.member_set", "organization", org_id, {"user_id": target.id, "role": body.role})
    db.commit()
    return {"user_id": target.id, "role": body.role}


# ---------------------------------------------------------------- settings & privacy
class SettingsIn(BaseModel):
    privacy_mode: str | None = Field(default=None, pattern=r"^(local_only|local_ai|pseudonymized_ai|cloud_ai)$")
    retention_days: int | None = Field(default=None, ge=0, le=36500)
    materiality_amount: Decimal | None = Field(default=None, ge=0)
    bank_match_window_days: int | None = Field(default=None, ge=0, le=30)
    vouching_threshold: Decimal | None = Field(default=None, ge=0)
    gst_filing: str | None = Field(default=None, pattern=r"^(monthly|qrmp)$")
    tds_applicable: bool | None = None
    tan: str | None = Field(default=None, pattern=r"^[A-Za-z]{4}[0-9]{5}[A-Za-z]$")
    clear_retention: bool = False


def _settings_out(s: CompanySettings) -> dict:
    return {"privacy_mode": s.privacy_mode, "retention_days": s.retention_days,
            "materiality_amount": S.m(s.materiality_amount), "bank_match_window_days": s.bank_match_window_days,
            "vouching_threshold": S.m(s.vouching_threshold), "gst_filing": s.gst_filing or "monthly",
            "tds_applicable": bool(s.tds_applicable), "tan": s.tan}


@router.get("/companies/{company_id}/settings")
def get_settings(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _settings_out(db.get(CompanySettings, acc.company.id))


@router.patch("/companies/{company_id}/settings")
def patch_settings(body: SettingsIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    s = db.get(CompanySettings, acc.company.id)
    changes = {}
    for f in ("privacy_mode", "retention_days", "materiality_amount", "bank_match_window_days", "vouching_threshold",
              "gst_filing", "tds_applicable", "tan"):
        v = getattr(body, f)
        if f == "tan" and v is not None:
            v = v.upper()
        if v is not None and getattr(s, f) != v:
            changes[f] = {"from": str(getattr(s, f)), "to": str(v)}
            setattr(s, f, v)
    if body.clear_retention and s.retention_days is not None:
        changes["retention_days"] = {"from": str(s.retention_days), "to": None}
        s.retention_days = None
    if changes:
        log_event(db, acc.company.id, acc.user.id, "settings.changed", "company", acc.company.id, changes)
    db.commit()
    return _settings_out(s)


@router.get("/companies/{company_id}/privacy")
def privacy_status(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    s = db.get(CompanySettings, acc.company.id)
    logs = db.scalars(select(AIRequestLog).where(AIRequestLog.company_id == acc.company.id)
                      .order_by(AIRequestLog.id.desc()).limit(20)).all()
    from ..assistant import cloud
    return {
        "privacy_mode": s.privacy_mode,
        "ai": provider_status(),
        "chat": cloud.status() if cloud.active() else {"configured": False},
        "data_flow": {
            "stays_local": ["Original documents (encrypted on disk)", "Extracted records and amounts", "Journal entries",
                            "Validation, duplicate and reconciliation results", "Audit imports and exceptions",
                            "Pseudonym mappings (in memory, per request)"],
            "may_leave_when_enabled": ["Only in 'pseudonymised external AI' mode, for bank-line classification only: direction, amount, month, a scrubbed description "
                                       "(names replaced by tokens; account numbers, references, GSTINs, PAN, e-mail and "
                                       "phone numbers removed), and the chart of accounts (codes, generic names, types).",
                                       "Only in 'Cloud AI' mode, for the chat assistant: your chat messages and the client "
                                       "data the assistant looks up to answer them (names, amounts, entries)."],
            "never_sent": ["Document files", "GSTINs", "Bank references and balances", "Other transactions", "Company name"],
        },
        "recent_ai_requests": [{"id": l.id, "provider": l.provider, "model": l.model, "purpose": l.purpose,
                                "status": l.status, "error": l.error, "sent_payload": l.sent_payload,
                                "input_tokens": l.input_tokens, "output_tokens": l.output_tokens,
                                "created_at": S.d(l.created_at)} for l in logs],
    }


@router.post("/companies/{company_id}/ai/test")
def ai_test(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    from ..services.ai_service import test_connection
    res = test_connection(db, acc.company, acc.user.id)
    db.commit()
    return res


@router.post("/companies/{company_id}/retention/apply")
def apply_retention(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    res = documents_service.apply_retention(db, acc.company, acc.user.id)
    db.commit()
    return res


# ---------------------------------------------------------------- chart of accounts
class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(pattern=r"^(asset|liability|equity|income|expense)$")
    role: str | None = Field(default=None, max_length=40)
    is_active: bool = True


@router.get("/companies/{company_id}/accounts")
def list_accounts(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return [S.account(a) for a in db.scalars(select(Account).where(Account.company_id == acc.company.id).order_by(Account.code))]


@router.post("/companies/{company_id}/accounts", status_code=201)
def create_account(body: AccountIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    if db.scalar(select(Account).where(Account.company_id == acc.company.id, Account.code == body.code.strip())):
        raise HTTPException(409, "An account with this code already exists.")
    a = Account(company_id=acc.company.id, code=body.code.strip(), name=body.name.strip(), type=body.type,
                role=body.role or None, is_active=body.is_active)
    db.add(a)
    db.flush()
    log_event(db, acc.company.id, acc.user.id, "account.created", "account", a.id, {"code": a.code, "name": a.name})
    db.commit()
    return S.account(a)


@router.patch("/companies/{company_id}/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    a = db.get(Account, account_id)
    if not a or a.company_id != acc.company.id:
        raise HTTPException(404, "Account not found.")
    before = S.account(a)
    a.name, a.type, a.role, a.is_active = body.name.strip(), body.type, body.role or None, body.is_active
    log_event(db, acc.company.id, acc.user.id, "account.updated", "account", a.id, {"before": before, "after": S.account(a)})
    db.commit()
    return S.account(a)


# ---------------------------------------------------------------- periods
class PeriodIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date


@router.get("/companies/{company_id}/periods")
def list_periods(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    ps = db.scalars(select(AccountingPeriod).where(AccountingPeriod.company_id == acc.company.id)
                    .order_by(AccountingPeriod.start_date)).all()
    return [{"id": p.id, "name": p.name, "start_date": S.d(p.start_date), "end_date": S.d(p.end_date), "locked": p.locked}
            for p in ps]


@router.post("/companies/{company_id}/periods", status_code=201)
def create_period(body: PeriodIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    if body.end_date < body.start_date:
        raise HTTPException(422, "The period ends before it starts.")
    for p in db.scalars(select(AccountingPeriod).where(AccountingPeriod.company_id == acc.company.id)):
        if p.start_date <= body.end_date and body.start_date <= p.end_date:
            raise HTTPException(409, f"Overlaps with period {p.name}.")
    p = AccountingPeriod(company_id=acc.company.id, name=body.name, start_date=body.start_date, end_date=body.end_date)
    db.add(p)
    db.flush()
    log_event(db, acc.company.id, acc.user.id, "period.created", "period", p.id, {"name": p.name})
    db.commit()
    return {"id": p.id}


class LockIn(BaseModel):
    locked: bool


@router.post("/companies/{company_id}/periods/{period_id}/lock")
def lock_period(period_id: int, body: LockIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("admin")
    p = db.get(AccountingPeriod, period_id)
    if not p or p.company_id != acc.company.id:
        raise HTTPException(404, "Period not found.")
    p.locked = body.locked
    log_event(db, acc.company.id, acc.user.id, "period.locked" if body.locked else "period.unlocked", "period", p.id, {})
    db.commit()
    return {"id": p.id, "locked": p.locked}


# ---------------------------------------------------------------- policies
class PolicyIn(BaseModel):
    kind: str = Field(pattern=r"^(bank_keyword|party_account|tds_category)$")
    definition: dict
    rationale: str = Field(min_length=1, max_length=2000)
    effective_from: date | None = None
    key: str | None = Field(default=None, max_length=80)


def _validate_policy(db: Session, company_id: int, kind: str, d: dict) -> dict:
    code = str(d.get("account_code") or "").strip()
    if not db.scalar(select(Account).where(Account.company_id == company_id, Account.code == code)):
        raise HTTPException(422, "The policy must name a ledger code from this company's chart of accounts.")
    if kind == "bank_keyword":
        kw = str(d.get("keyword") or "").strip()
        if len(kw) < 3:
            raise HTTPException(422, "The keyword must have at least 3 characters.")
        direction = d.get("direction")
        if direction not in (None, "", "in", "out"):
            raise HTTPException(422, "Direction must be 'in', 'out' or empty.")
        return {"keyword": kw, "account_code": code, "direction": direction or None}
    if kind == "party_account":
        if not (d.get("gstin") or d.get("party")):
            raise HTTPException(422, "Give the party GSTIN or name.")
        return {"gstin": (d.get("gstin") or "").upper() or None, "party": d.get("party") or None, "account_code": code}
    try:
        rate = Decimal(str(d.get("rate_percent")))
    except Exception:
        raise HTTPException(422, "Enter the TDS rate (percent) that your firm has verified for this category.")
    if not (Decimal("0") < rate <= Decimal("100")):
        raise HTTPException(422, "The TDS rate must be between 0 and 100.")
    label = str(d.get("section_label") or "").strip()
    if not label:
        raise HTTPException(422, "Enter the TDS section label (for example the section your firm applies).")
    return {"account_code": code, "party": d.get("party") or None, "rate_percent": str(rate), "section_label": label}


@router.get("/companies/{company_id}/policies")
def list_policies(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    ps = db.scalars(select(Policy).where(Policy.company_id == acc.company.id).order_by(Policy.key, Policy.version.desc())).all()
    return [S.policy(p) for p in ps]


@router.post("/companies/{company_id}/policies", status_code=201)
def create_policy(body: PolicyIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    definition = _validate_policy(db, acc.company.id, body.kind, body.definition)
    key = body.key or (f"kw:{definition['keyword'].upper()}" if body.kind == "bank_keyword" else
                       f"party:{(definition.get('gstin') or definition.get('party') or '').upper()}" if body.kind == "party_account"
                       else f"tds:{definition['account_code']}")
    latest = db.scalar(select(Policy).where(Policy.company_id == acc.company.id, Policy.key == key)
                       .order_by(Policy.version.desc()))
    p = Policy(company_id=acc.company.id, key=key, version=(latest.version + 1) if latest else 1, kind=body.kind,
               definition=definition, rationale=body.rationale, effective_from=body.effective_from,
               created_by=acc.user.id, status="proposed")
    db.add(p)
    db.flush()
    log_event(db, acc.company.id, acc.user.id, "policy.proposed", "policy", p.id, {"key": key, "version": p.version})
    db.commit()
    return S.policy(p)


class PolicyDecisionIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|disable)$")
    note: str | None = None


@router.post("/companies/{company_id}/policies/{policy_id}/decision")
def decide_policy(policy_id: int, body: PolicyDecisionIn, acc: CompanyAccess = Depends(company_access),
                  db: Session = Depends(get_db)):
    acc.require("admin")
    p = db.get(Policy, policy_id)
    if not p or p.company_id != acc.company.id:
        raise HTTPException(404, "Policy not found.")
    if body.decision == "approve":
        if p.status != "proposed":
            raise HTTPException(409, "Only proposed policies can be approved.")
        for old in db.scalars(select(Policy).where(Policy.company_id == acc.company.id, Policy.key == p.key,
                                                   Policy.status == "approved")):
            old.status = "superseded"
        p.status, p.approved_by, p.approved_at = "approved", acc.user.id, utcnow()
    else:
        p.status = "disabled"
    log_event(db, acc.company.id, acc.user.id, f"policy.{body.decision}d", "policy", p.id,
              {"key": p.key, "version": p.version, "note": body.note})
    db.commit()
    return S.policy(p)
