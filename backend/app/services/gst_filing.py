"""Approval-gated GST return workflow: prepare -> notify -> approve -> portal preparation -> human filing -> verify.

The state lives in the database (GstReturn, GstReturnSnapshot, GstPortalJob), so every step can resume after a
crash or an expired portal session. Rules enforced here:
- Approval is bound to one snapshot (id, version, payload hash) and to the source-data fingerprint. Any change to
  the period's records or entries invalidates the approval before portal work can start or continue.
- Every state change is a compare-and-set on lock_version: two approvals, two job starts or two workers racing
  each other cannot both succeed.
- Blocking warnings need resolution, or an override with a written reason by an owner/admin.
- FinV never submits or files a return and never handles portal passwords, OTPs, EVC or DSC. After the person
  files on the portal, FinV records "filing_reported" and only shows "verified" once the portal shows the ARN.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..engine import gst_returns as gr
from ..engine.gst import validate_gstin
from ..models import (Company, GstPortalJob, GstReturn, GstReturnSnapshot, GstReviewLink, JournalEntry, Membership,
                      SourceRecord, User, utcnow)
from ..security import verify_password
from . import notify
from .audit_log import log_event

DOC_TYPES = ("sales_invoice", "purchase_invoice", "credit_note", "debit_note")
OVERRIDE_ROLES = {"owner", "admin"}
LEASE = timedelta(minutes=3)      # a worker that has not checkpointed for this long is presumed dead

# Allowed state changes. FinV itself never moves a return to "filed": filing_reported is set when a person says they
# filed on the portal, verified only when the portal shows the acknowledgement.
TRANSITIONS = {
    "prepared": {"prepared", "awaiting_approval", "approved", "invalidated", "cancelled"},
    "awaiting_approval": {"prepared", "approved", "invalidated", "cancelled"},
    "approved": {"prepared", "portal_in_progress", "invalidated", "cancelled"},
    "portal_in_progress": {"portal_ready_for_review", "portal_failed", "invalidated"},
    "portal_failed": {"prepared", "portal_in_progress", "invalidated", "cancelled"},
    "portal_ready_for_review": {"prepared", "portal_in_progress", "filing_reported", "invalidated", "cancelled"},
    "filing_reported": {"filing_reported", "verified"},
    "verified": set(),
    "invalidated": {"prepared", "cancelled"},
    "cancelled": {"prepared"},
}
# States in which a change to the source data cancels the approval / portal work.
GUARDED = {"prepared", "awaiting_approval", "approved", "portal_in_progress", "portal_failed",
           "portal_ready_for_review"}


class FilingError(Exception):
    def __init__(self, message: str, status: int = 409, details: dict | None = None):
        super().__init__(message)
        self.message, self.status, self.details = message, status, details or {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ data
def collect_rows(db: Session, company: Company, period: str) -> tuple[list[gr.DocRow], list[int]]:
    start, end = gr.period_bounds(period)
    recs = db.scalars(select(SourceRecord).where(
        SourceRecord.company_id == company.id, SourceRecord.record_type.in_(DOC_TYPES),
        or_(SourceRecord.doc_date.is_(None), SourceRecord.doc_date.between(start, end)))).all()
    entries: dict[int, JournalEntry] = {}
    if recs:
        for e in db.scalars(select(JournalEntry).where(JournalEntry.source_record_id.in_([r.id for r in recs]))
                            .order_by(JournalEntry.id)).all():
            cur = entries.get(e.source_record_id)
            if cur is None or cur.status == "rejected" or e.status != "rejected":
                entries[e.source_record_id] = e
    rows, undated = [], []
    for r in recs:
        if r.doc_date is None:
            if r.status not in {"duplicate", "rejected"}:
                undated.append(r.id)
            continue
        e = entries.get(r.id)
        rows.append(gr.DocRow(record_id=r.id, record_type=r.record_type, note_side=r.note_side, status=r.status,
                              entry_id=e.id if e else None, entry_version=e.version if e else None,
                              entry_status=e.status if e else None, party_gstin=r.party_gstin,
                              doc_number=r.doc_number, doc_date=r.doc_date, place_of_supply=r.place_of_supply,
                              taxable=r.taxable_amount, cgst=r.cgst, sgst=r.sgst, igst=r.igst, cess=r.cess,
                              total=r.total_amount, original_record_id=r.original_record_id))
    return rows, undated


def current_fingerprint(db: Session, company: Company, ret: GstReturn) -> str:
    rows, undated = collect_rows(db, company, ret.period)
    return gr.fingerprint(company.gstin or "", ret.return_type, ret.period, rows, undated)


def _reconciliation(db: Session, company: Company) -> dict:
    from . import recon_service
    try:
        r = recon_service.run(db, company)
    except Exception:   # reconciliation is informational here; never block preparation on it
        return {"status": "unavailable"}
    open_items = len(r["unmatched_bank"]) + len(r["ambiguous"])
    return {"status": "clear" if open_items == 0 else "open_items", "matched": len(r["matched"]),
            "unmatched_bank": len(r["unmatched_bank"]), "ambiguous": len(r["ambiguous"]),
            "unmatched_book": len(r["unmatched_book"])}


def snapshot(db: Session, ret: GstReturn) -> GstReturnSnapshot | None:
    return db.get(GstReturnSnapshot, ret.current_snapshot_id) if ret.current_snapshot_id else None


# ------------------------------------------------------------------ state machine
def transition(db: Session, ret: GstReturn, to: str, user_id: int | None, reason: str | None = None,
               details: dict | None = None, **fields) -> GstReturn:
    """Compare-and-set state change, recorded in the audit trail."""
    frm = ret.state
    if to not in TRANSITIONS[frm]:
        raise FilingError(f"A return that is {frm.replace('_', ' ')} cannot move to {to.replace('_', ' ')}.")
    res = db.execute(update(GstReturn).where(GstReturn.id == ret.id, GstReturn.state == frm,
                                             GstReturn.lock_version == ret.lock_version)
                     .values(state=to, lock_version=GstReturn.lock_version + 1, state_reason=reason,
                             updated_at=utcnow(), **fields)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise FilingError("Someone else changed this return at the same moment. Reload and try again.")
    db.refresh(ret)
    log_event(db, ret.company_id, user_id, f"gst.{to}", "gst_return", ret.id,
              {"from": frm, "to": to, "return_type": ret.return_type, "period": ret.period,
               "snapshot_id": ret.current_snapshot_id, "reason": reason, **(details or {})})
    return ret


def check_fresh(db: Session, company: Company, ret: GstReturn) -> bool:
    """Invalidate the return if its source data changed since the current snapshot. Returns True if still fresh."""
    snap = snapshot(db, ret)
    if snap is None or ret.state not in GUARDED:
        return True
    if current_fingerprint(db, company, ret) == snap.source_fingerprint and company.gstin == snap.gstin:
        return True
    transition(db, ret, "invalidated", None,
               "The accounting data for this period changed after the return was prepared. Prepare it again "
               "and review the new figures.", approved_snapshot_id=None)
    return False


def prepare(db: Session, company: Company, user_id: int, return_type: str, period: str) -> GstReturn:
    if return_type not in gr.RETURN_TYPES:
        raise FilingError("Return type must be GSTR1 or GSTR3B.", 422)
    try:
        gr.period_bounds(period)
    except (ValueError, TypeError):
        raise FilingError("Tax period must look like 2025-06.", 422)
    if not company.gstin or not validate_gstin(company.gstin)[0]:
        raise FilingError("Add a valid GSTIN for this company in Settings before preparing a return.", 422)
    ret = db.scalar(select(GstReturn).where(GstReturn.company_id == company.id, GstReturn.return_type == return_type,
                                            GstReturn.period == period))
    if ret is None:
        ret = GstReturn(company_id=company.id, return_type=return_type, period=period, state="prepared")
        db.add(ret)
        db.flush()
        created = True
    else:
        created = False
        if ret.state in {"filing_reported", "verified"}:
            raise FilingError("This return has already been filed. Corrections go in a later return or amendment.")
        if ret.state == "portal_in_progress":
            raise FilingError("Portal preparation is running for this return. Wait for it to finish or fail.")
    rows, undated = collect_rows(db, company, period)
    fp = gr.fingerprint(company.gstin, return_type, period, rows, undated)
    cur = snapshot(db, ret)
    if cur and cur.source_fingerprint == fp and cur.gstin == company.gstin and ret.state in {
            "prepared", "awaiting_approval", "approved", "portal_ready_for_review"}:
        return ret      # nothing changed: keep the existing snapshot (and any approval bound to it)
    res = gr.build(return_type, company.gstin, period, rows, undated)
    version = (cur.version if cur else 0) + 1
    snap = GstReturnSnapshot(return_id=ret.id, company_id=company.id, version=version, gstin=company.gstin,
                             return_type=return_type, period=period, rules_version=gr.RULES_VERSION,
                             totals=res.totals, breakdown=res.breakdown, warnings=res.warnings,
                             reconciliation=_reconciliation(db, company), source_fingerprint=fp,
                             record_ids=res.record_ids, payload=res.payload, payload_sha256=gr.payload_hash(res.payload),
                             created_by=user_id)
    db.add(snap)
    db.flush()
    if created:
        ret.current_snapshot_id = snap.id
        db.flush()
        log_event(db, company.id, user_id, "gst.prepared", "gst_return", ret.id,
                  {"return_type": return_type, "period": period, "snapshot_id": snap.id, "version": version,
                   "payload_sha256": snap.payload_sha256})
    else:
        transition(db, ret, "prepared", user_id, f"Prepared version {version}.",
                   {"version": version, "payload_sha256": snap.payload_sha256},
                   current_snapshot_id=snap.id, approved_snapshot_id=None, approved_by=None, approved_at=None,
                   override=None)
    return ret


def send_for_approval(db: Session, company: Company, ret: GstReturn, user_id: int) -> dict:
    if ret.state not in {"prepared", "awaiting_approval"}:
        raise FilingError("Only a prepared return can be sent for approval.")
    if not check_fresh(db, company, ret):
        raise FilingError(ret.state_reason)
    snap = snapshot(db, ret)
    configured = notify.sender().configured()
    link = None
    if configured:
        token = secrets.token_urlsafe(32)
        db.add(GstReviewLink(return_id=ret.id, snapshot_id=snap.id, token_hash=_hash(token), channel="telegram",
                             expires_at=utcnow() + timedelta(minutes=settings.review_link_minutes)))
        link = f"{settings.public_url}/gst/review?t={token}"
    blocking = sum(1 for w in snap.warnings if w["severity"] == "blocking")
    text = notify.approval_message(company.id, snap.gstin, ret.return_type, ret.period,
                                   gr.tax_total(ret.return_type, snap.totals), blocking, len(snap.warnings) - blocking,
                                   link)
    channel = notify.send(text, _forbidden_words(db, company))
    if ret.state == "prepared":
        transition(db, ret, "awaiting_approval", user_id, "Sent for approval.",
                   {"channel": channel, "snapshot_version": snap.version})
    else:
        log_event(db, company.id, user_id, "gst.notified", "gst_return", ret.id, {"channel": channel})
    return {"channel": channel, "sent": channel != "not_configured"}


def _forbidden_words(db: Session, company: Company) -> list[str]:
    names = db.scalars(select(SourceRecord.party_name).where(SourceRecord.company_id == company.id,
                                                             SourceRecord.party_name.isnot(None)).distinct()).all()
    return [company.name, company.gstin or "", *names]


def redeem_link(db: Session, user: User, token: str) -> dict:
    """Opens the review screen for a signed-in member. Single use; it never approves anything."""
    link = db.scalar(select(GstReviewLink).where(GstReviewLink.token_hash == _hash(token or "")))
    if link is None:
        raise FilingError("This review link is not valid.", 404)
    ret = db.get(GstReturn, link.return_id)
    company = db.get(Company, ret.company_id)
    member = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                Membership.organization_id == company.organization_id))
    if member is None:
        raise FilingError("This review link is not valid.", 404)
    if link.used_at is not None:
        raise FilingError("This review link has already been used. Open the return from FinV instead.", 410)
    if link.expires_at < utcnow():
        raise FilingError("This review link has expired. Open the return from FinV instead.", 410)
    res = db.execute(update(GstReviewLink).where(GstReviewLink.id == link.id, GstReviewLink.used_at.is_(None))
                     .values(used_at=utcnow(), used_by=user.id).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise FilingError("This review link has already been used. Open the return from FinV instead.", 410)
    log_event(db, company.id, user.id, "gst.review_link_used", "gst_return", ret.id, {"link_id": link.id})
    return {"company_id": company.id, "return_id": ret.id,
            "snapshot_is_current": link.snapshot_id == ret.current_snapshot_id}


def approve(db: Session, company: Company, ret: GstReturn, user: User, role: str, snapshot_id: int, version: int,
            payload_sha256: str, password: str, override_reason: str | None) -> GstReturn:
    if not verify_password(password or "", user.password_hash):
        log_event(db, company.id, user.id, "gst.approval_refused", "gst_return", ret.id, {"why": "password"})
        db.commit()
        raise FilingError("Your password is incorrect. The return was not approved.", 403)
    if ret.state not in {"prepared", "awaiting_approval"}:
        raise FilingError(f"This return is {ret.state.replace('_', ' ')} and cannot be approved now.")
    snap = snapshot(db, ret)
    if snap is None or snap.id != snapshot_id or snap.version != version or snap.payload_sha256 != payload_sha256:
        raise FilingError("These figures are not the latest version of the return. Reload and review again.")
    if not check_fresh(db, company, ret):
        raise FilingError(ret.state_reason)
    blocking = [w for w in snap.warnings if w["severity"] == "blocking"]
    override = None
    if blocking:
        reason = (override_reason or "").strip()
        if not reason:
            raise FilingError("Resolve the warnings marked 'must resolve', or have an owner/admin approve with a "
                              "written override reason.", 409, {"blocking": [w["code"] for w in blocking]})
        if role not in OVERRIDE_ROLES:
            raise FilingError("Only an owner or admin can override warnings that affect correctness.", 403)
        if len(reason) < 15:
            raise FilingError("Explain the override in a little more detail (at least 15 characters).", 422)
        override = {"reason": reason, "by": user.id, "warnings": [w["code"] for w in blocking]}
    return transition(db, ret, "approved", user.id, "Approved for portal preparation.",
                      {"snapshot_id": snap.id, "version": snap.version, "payload_sha256": snap.payload_sha256,
                       "override": override},
                      approved_snapshot_id=snap.id, approved_by=user.id, approved_at=utcnow(), override=override)


def cancel(db: Session, ret: GstReturn, user_id: int, reason: str) -> GstReturn:
    if not (reason or "").strip():
        raise FilingError("Give a reason for cancelling.", 422)
    return transition(db, ret, "cancelled", user_id, reason.strip(), approved_snapshot_id=None)


# ------------------------------------------------------------------ portal jobs
def start_portal_job(db: Session, company: Company, ret: GstReturn, user_id: int) -> GstPortalJob:
    if ret.state not in {"approved", "portal_failed", "portal_ready_for_review"}:
        raise FilingError("Portal preparation needs an approved return.")
    if not check_fresh(db, company, ret):
        raise FilingError(ret.state_reason)
    if not ret.approved_snapshot_id or ret.approved_snapshot_id != ret.current_snapshot_id:
        raise FilingError("The approval does not cover the current figures. Approve the return again.")
    job = db.scalar(select(GstPortalJob).where(GstPortalJob.return_id == ret.id,
                                               GstPortalJob.snapshot_id == ret.approved_snapshot_id,
                                               GstPortalJob.kind == "prepare"))
    if job is None:
        job = GstPortalJob(return_id=ret.id, snapshot_id=ret.approved_snapshot_id, kind="prepare", checkpoints={},
                           created_by=user_id)
        db.add(job)
        db.flush()
    elif job.state == "failed":
        job.state, job.error = "queued", None     # resume from its checkpoints
    transition(db, ret, "portal_in_progress", user_id, "Portal preparation started.", {"job_id": job.id})
    return job


def report_filed(db: Session, company: Company, ret: GstReturn, user_id: int, arn: str | None) -> GstPortalJob:
    """The person says they filed on the portal. FinV records it and creates a job to check the portal."""
    arn = (arn or "").strip().upper() or None
    if arn and not (10 <= len(arn) <= 20 and arn.isalnum()):
        raise FilingError("The ARN should be 10-20 letters and digits.", 422)
    if ret.state == "portal_ready_for_review":
        if not check_fresh(db, company, ret):
            raise FilingError(ret.state_reason)
        transition(db, ret, "filing_reported", user_id, "Filing reported by the authorised person; not yet verified.",
                   {"reported_arn": arn}, reported_arn=arn)
    elif ret.state == "filing_reported":
        if arn:
            ret.reported_arn = arn
            log_event(db, company.id, user_id, "gst.filing_reported", "gst_return", ret.id, {"reported_arn": arn})
    else:
        raise FilingError("Mark a return as filed only after FinV has prepared it on the portal and you filed it there.")
    job = db.scalar(select(GstPortalJob).where(GstPortalJob.return_id == ret.id,
                                               GstPortalJob.snapshot_id == ret.approved_snapshot_id,
                                               GstPortalJob.kind == "verify"))
    if job is None:
        job = GstPortalJob(return_id=ret.id, snapshot_id=ret.approved_snapshot_id, kind="verify", checkpoints={},
                           created_by=user_id)
        db.add(job)
        db.flush()
    elif job.state in {"failed", "done"}:
        job.state, job.error = "queued", None
    return job


def claim_job(db: Session, job_id: int, worker_id: str) -> tuple[GstPortalJob, GstReturn, GstReturnSnapshot]:
    """Take the lease on a job. Refuses if another worker holds a live lease or the approval no longer holds."""
    job = db.get(GstPortalJob, job_id)
    if job is None:
        raise FilingError("Portal job not found.", 404)
    ret = db.get(GstReturn, job.return_id)
    company = db.get(Company, ret.company_id)
    if job.state == "done":
        raise FilingError("This portal job has already finished.")
    want = "portal_in_progress" if job.kind == "prepare" else "filing_reported"
    if ret.state != want:
        raise FilingError(f"The return is {ret.state.replace('_', ' ')}, so this job cannot run.")
    if job.kind == "prepare":
        if not check_fresh(db, company, ret):
            db.commit()
            raise FilingError(ret.state_reason)
        if ret.approved_snapshot_id != job.snapshot_id or ret.current_snapshot_id != job.snapshot_id:
            raise FilingError("The approval does not cover this job's figures.")
    now = utcnow()
    res = db.execute(update(GstPortalJob).where(
        GstPortalJob.id == job.id, GstPortalJob.state.in_(["queued", "running", "failed"]),
        or_(GstPortalJob.worker_id.is_(None), GstPortalJob.worker_id == worker_id,
            GstPortalJob.heartbeat_at < now - LEASE))
        .values(state="running", worker_id=worker_id, heartbeat_at=now, attempts=GstPortalJob.attempts + 1)
        .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise FilingError("Another FinV window is already running this portal job.")
    db.refresh(job)
    log_event(db, ret.company_id, None, "gst.portal_job_claimed", "gst_return", ret.id,
              {"job_id": job.id, "kind": job.kind, "attempt": job.attempts, "resumed_from": sorted(job.checkpoints)})
    db.commit()
    return job, ret, db.get(GstReturnSnapshot, job.snapshot_id)


def checkpoint(db: Session, job: GstPortalJob, worker_id: str, name: str, data=True) -> None:
    """Record a completed step. Fails if the lease was lost, or if the approval was invalidated meanwhile."""
    ret = db.get(GstReturn, job.return_id)
    db.refresh(ret)
    if job.kind == "prepare":
        company = db.get(Company, ret.company_id)
        if ret.state != "portal_in_progress" or not check_fresh(db, company, ret):
            db.commit()
            raise FilingError("The return changed while the portal job was running; stopped.")
    cps = dict(job.checkpoints or {})
    cps[name] = data
    res = db.execute(update(GstPortalJob).where(GstPortalJob.id == job.id, GstPortalJob.worker_id == worker_id)
                     .values(checkpoints=cps, heartbeat_at=utcnow()).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise FilingError("This worker lost the job to another window; stopped.")
    log_event(db, ret.company_id, None, "gst.portal_step", "gst_return", ret.id, {"job_id": job.id, "step": name})
    db.commit()
    db.refresh(job)


def compare_totals(snap: GstReturnSnapshot, portal: dict) -> list[dict]:
    keys = ("taxable", "igst", "cgst", "sgst", "cess") if snap.return_type == "GSTR1" else (
        "outward_taxable", "output_igst", "output_cgst", "output_sgst", "output_cess",
        "itc_igst", "itc_cgst", "itc_sgst", "itc_cess")
    diffs = []
    for k in keys:
        mine = Decimal(snap.totals[k])
        theirs = portal.get(k)
        if theirs is None:
            diffs.append({"field": k, "finv": str(mine), "portal": None})
        elif Decimal(str(theirs)).quantize(Decimal("0.01")) != mine:
            diffs.append({"field": k, "finv": str(mine), "portal": str(Decimal(str(theirs)).quantize(Decimal("0.01")))})
    return diffs


def finish_prepare(db: Session, job: GstPortalJob, worker_id: str, portal_totals: dict) -> list[dict]:
    snap = db.get(GstReturnSnapshot, job.snapshot_id)
    ret = db.get(GstReturn, job.return_id)
    diffs = compare_totals(snap, portal_totals)
    job.portal_totals, job.differences, job.worker_id = portal_totals, diffs, None
    if diffs:
        job.state, job.error = "failed", "The portal totals differ from the approved figures."
        transition(db, ret, "portal_failed", None, "The portal totals differ from the approved figures. Nothing was "
                   "filed. Check the differences before trying again.", {"job_id": job.id, "differences": diffs})
    else:
        job.state = "done"
        transition(db, ret, "portal_ready_for_review", None,
                   "The data is on the portal and its totals match the approved figures. Review them on the portal, "
                   "then file it there yourself (payment, OTP/EVC or DSC).", {"job_id": job.id})
        _status_note(db, ret, "data is on the portal and matches. Review the portal figures and file it yourself.")
    db.commit()
    return diffs


def fail_job(db: Session, job: GstPortalJob, worker_id: str, error: str) -> None:
    db.rollback()
    db.refresh(job)
    ret = db.get(GstReturn, job.return_id)
    job.state, job.error, job.worker_id = "failed", error[:500], None
    if job.kind == "prepare" and ret.state == "portal_in_progress":
        transition(db, ret, "portal_failed", None, f"Portal preparation stopped: {error[:300]} Nothing was filed. "
                   "You can resume it; completed steps are not repeated.", {"job_id": job.id})
    else:
        log_event(db, ret.company_id, None, "gst.portal_job_failed", "gst_return", ret.id,
                  {"job_id": job.id, "kind": job.kind, "error": error[:300]})
    db.commit()


def record_verification(db: Session, job: GstPortalJob, worker_id: str, status: str | None, arn: str | None,
                        filed_at: str | None) -> bool:
    """Store what the portal shows. Only a 'Filed' status with an ARN marks the return verified."""
    ret = db.get(GstReturn, job.return_id)
    job.portal_totals = {"status": status, "arn": arn, "filed_at": filed_at}
    job.worker_id = None
    if (status or "").strip().lower() == "filed" and arn:
        job.state = "done"
        transition(db, ret, "verified", None, "The portal shows the return as filed.",
                   {"arn": arn, "portal_status": status, "filed_at": filed_at, "reported_arn": ret.reported_arn,
                    "arn_matches_reported": (ret.reported_arn or arn) == arn},
                   arn=arn, portal_status=status, filed_at=filed_at, verified_at=utcnow())
        _status_note(db, ret, f"filing verified on the portal (ARN ending {arn[-4:]}).")
        db.commit()
        return True
    job.state, job.error = "failed", f"The portal does not show this return as filed yet (status: {status or 'unknown'})."
    ret.portal_status = status
    log_event(db, ret.company_id, None, "gst.verification_pending", "gst_return", ret.id,
              {"job_id": job.id, "portal_status": status})
    db.commit()
    return False


def _status_note(db: Session, ret: GstReturn, text: str) -> None:
    """Best-effort Telegram update; the workflow never depends on it."""
    company = db.get(Company, ret.company_id)
    try:
        notify.send(notify.status_message(company.id, company.gstin, ret.return_type, ret.period, text),
                    _forbidden_words(db, company))
    except notify.NotifyError:
        pass
