"""GST returns: prepare, send for approval, approve, portal preparation, filing verification."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BACKEND_DIR
from ..db import get_db
from ..engine.gst_returns import tax_total
from ..models import AuditEvent, GstPortalJob, GstReturn, GstReturnSnapshot, User
from ..portal import launch
from ..security import CompanyAccess, company_access, current_user
from ..services import gst_filing as gf
from ..services import notify
from . import serializers as S

router = APIRouter(prefix="/api/companies/{company_id}/gst", tags=["gst"])
links = APIRouter(prefix="/api/gst", tags=["gst"])
WORKER = f"{BACKEND_DIR / '.venv/bin/python'} {BACKEND_DIR.parent / 'scripts/gst_portal.py'}"


class PrepareIn(BaseModel):
    return_type: str = Field(pattern="^(GSTR1|GSTR3B)$")
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ApproveIn(BaseModel):
    snapshot_id: int
    version: int
    payload_sha256: str = Field(min_length=64, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    override_reason: str | None = Field(default=None, max_length=1000)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class FiledIn(BaseModel):
    arn: str | None = Field(default=None, max_length=40)


class RedeemIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)


def _ret(db: Session, acc: CompanyAccess, rid: int) -> GstReturn:
    r = db.get(GstReturn, rid)
    if r is None or r.company_id != acc.company.id:
        raise HTTPException(404, "Return not found.")
    return r


def _fail(db: Session, exc: gf.FilingError):
    db.commit()     # keep audit events and invalidations recorded before the refusal
    raise HTTPException(exc.status, {"message": exc.message, **exc.details} if exc.details else exc.message)


def _snap(s: GstReturnSnapshot | None) -> dict | None:
    if s is None:
        return None
    return {"id": s.id, "version": s.version, "gstin": s.gstin, "return_type": s.return_type, "period": s.period,
            "rules_version": s.rules_version, "totals": s.totals, "breakdown": s.breakdown, "warnings": s.warnings,
            "reconciliation": s.reconciliation, "payload_sha256": s.payload_sha256,
            "source_fingerprint": s.source_fingerprint, "documents": len(s.record_ids),
            "tax_total": S.m(tax_total(s.return_type, s.totals)), "created_at": S.d(s.created_at)}


def _job(j: GstPortalJob | None) -> dict | None:
    if j is None:
        return None
    return {"id": j.id, "kind": j.kind, "state": j.state, "steps": sorted(j.checkpoints or {}), "attempts": j.attempts,
            "error": j.error, "differences": j.differences, "portal_totals": j.portal_totals,
            "command": f"{WORKER} {j.id}", "updated_at": S.d(j.updated_at)}


def _detail(db: Session, r: GstReturn) -> dict:
    jobs = db.scalars(select(GstPortalJob).where(GstPortalJob.return_id == r.id).order_by(GstPortalJob.id.desc())).all()
    events = db.scalars(select(AuditEvent).where(AuditEvent.object_type == "gst_return", AuditEvent.object_id == r.id,
                                                 AuditEvent.company_id == r.company_id)
                        .order_by(AuditEvent.id.desc()).limit(100)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(
        User.id.in_({e.user_id for e in events if e.user_id} | ({r.approved_by} if r.approved_by else set())))).all()}
    return {"id": r.id, "return_type": r.return_type, "period": r.period, "state": r.state, "reason": r.state_reason,
            "lock_version": r.lock_version, "snapshot": _snap(gf.snapshot(db, r)),
            "approved_snapshot_id": r.approved_snapshot_id, "approved_by": names.get(r.approved_by),
            "approved_at": S.d(r.approved_at), "override": r.override, "arn": r.arn, "portal_status": r.portal_status,
            "filed_at": r.filed_at, "verified_at": S.d(r.verified_at), "reported_arn": r.reported_arn,
            "prepare_job": _job(next((j for j in jobs if j.kind == "prepare"), None)),
            "verify_job": _job(next((j for j in jobs if j.kind == "verify"), None)),
            "telegram_configured": notify.sender().configured(), "can_open_window": launch.can_launch(),
            "history": [{"at": S.d(e.created_at), "action": e.action, "by": names.get(e.user_id, "FinV"),
                         "details": e.details} for e in events]}


@router.get("/returns")
def list_returns(acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    rets = db.scalars(select(GstReturn).where(GstReturn.company_id == acc.company.id)
                      .order_by(GstReturn.period.desc(), GstReturn.return_type)).all()
    out = []
    for r in rets:
        gf.check_fresh(db, acc.company, r)
        s = gf.snapshot(db, r)
        out.append({"id": r.id, "return_type": r.return_type, "period": r.period, "state": r.state,
                    "version": s.version if s else None, "arn": r.arn,
                    "tax_total": S.m(tax_total(r.return_type, s.totals)) if s else None,
                    "blocking": sum(1 for w in s.warnings if w["severity"] == "blocking") if s else 0})
    db.commit()
    return out


@router.post("/returns", status_code=201)
def prepare(body: PrepareIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    acc.require("prepare")
    try:
        r = gf.prepare(db, acc.company, acc.user.id, body.return_type, body.period)
    except gf.FilingError as exc:
        _fail(db, exc)
    db.commit()
    return _detail(db, r)


@router.get("/returns/{rid}")
def get_return(rid: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    r = _ret(db, acc, rid)
    gf.check_fresh(db, acc.company, r)
    db.commit()
    return _detail(db, r)


@router.get("/returns/{rid}/payload.json")
def payload(rid: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """The approved portal data, for a manual upload. Only available once approved."""
    acc.require("prepare")
    r = _ret(db, acc, rid)
    gf.check_fresh(db, acc.company, r)
    db.commit()
    if not r.approved_snapshot_id or r.approved_snapshot_id != r.current_snapshot_id or r.state in {
            "invalidated", "cancelled"}:
        raise HTTPException(409, "The portal file is available only for an approved, current return.")
    import json
    s = db.get(GstReturnSnapshot, r.approved_snapshot_id)
    return Response(json.dumps(s.payload, separators=(",", ":")), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{r.return_type}_{r.period}_v{s.version}.json"'})


def _act(fn):
    def run(rid: int, acc: CompanyAccess, db: Session, perm: str, *args):
        acc.require(perm)
        r = _ret(db, acc, rid)
        try:
            fn(db, acc, r, *args)
        except gf.FilingError as exc:
            _fail(db, exc)
        db.commit()
        db.refresh(r)
        return _detail(db, r)
    return run


@router.post("/returns/{rid}/send")
def send_for_approval(rid: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    def go(db, acc, r):
        try:
            gf.send_for_approval(db, acc.company, r, acc.user.id)
        except notify.NotifyError as exc:
            raise gf.FilingError(str(exc), 502)
    return _act(go)(rid, acc, db, "prepare")


@router.post("/returns/{rid}/approve")
def approve(rid: int, body: ApproveIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _act(lambda db, acc, r: gf.approve(db, acc.company, r, acc.user, acc.role, body.snapshot_id, body.version,
                                              body.payload_sha256, body.password, body.override_reason)
                )(rid, acc, db, "approve")


@router.post("/returns/{rid}/cancel")
def cancel(rid: int, body: ReasonIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _act(lambda db, acc, r: gf.cancel(db, r, acc.user.id, body.reason))(rid, acc, db, "approve")


def _open_window(d: dict) -> dict:
    """Open the portal browser window for the job that is due, if this computer has a screen."""
    job = d["prepare_job"] if d["state"] == "portal_in_progress" else d["verify_job"] if d["state"] == "filing_reported" else None
    d["window_opened"] = bool(job and job["state"] != "done" and launch.launch(job["id"]))
    return d


@router.post("/returns/{rid}/portal-job")
def start_portal_job(rid: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _open_window(_act(lambda db, acc, r: gf.start_portal_job(db, acc.company, r, acc.user.id))(
        rid, acc, db, "approve"))


@router.post("/returns/{rid}/filed")
def report_filed(rid: int, body: FiledIn, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    return _open_window(_act(lambda db, acc, r: gf.report_filed(db, acc.company, r, acc.user.id, body.arn))(
        rid, acc, db, "approve"))


@router.post("/returns/{rid}/open-window")
def open_window(rid: int, acc: CompanyAccess = Depends(company_access), db: Session = Depends(get_db)):
    """Open the portal window again (e.g. it was closed before signing in). The job resumes where it stopped."""
    acc.require("approve")
    d = _detail(db, _ret(db, acc, rid))
    if d["state"] not in {"portal_in_progress", "filing_reported"}:
        raise HTTPException(409, "There is no portal step waiting for this return.")
    if not launch.can_launch():
        raise HTTPException(409, "FinV cannot open a window on this computer. Run the command shown instead.")
    return _open_window(d)


@links.post("/review-links/redeem")
def redeem(body: RedeemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        out = gf.redeem_link(db, user, body.token)
    except gf.FilingError as exc:
        _fail(db, exc)
    db.commit()
    return out
