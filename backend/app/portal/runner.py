"""Runs one GST portal job step by step, saving a checkpoint after each step so it can resume safely.

The runner talks to the portal only through an adapter (gst_portal.PlaywrightPortal, or a fake in tests). It never
receives credentials: the person signs in to the portal themselves. Before every step it re-checks that the return
is still approved and its data unchanged (gst_filing.checkpoint does this), and it stops at the final review point.

Resume rules:
- GSTR-1 upload: "upload_started" is saved before the file is sent. If a run dies before the portal's reference
  is saved, the next run first looks for that upload in the portal's upload history and adopts it, rather than
  uploading again blindly.
- GSTR-3B: saving the same values again is harmless, so the fill step simply repeats if it was not completed.
- Nothing in the runner submits, files or signs a return; the adapter refuses such clicks outright.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import utcnow
from ..services import gst_filing as gf

log = logging.getLogger("finv.portal")
UPLOAD_POLL_S = 10
UPLOAD_WAIT_S = 600


class PortalError(RuntimeError):
    """The portal did not look or behave as expected. Nothing was filed."""


def run_job(db: Session, job_id: int, portal, worker_id: str | None = None, sleep=time.sleep) -> str:
    """Returns the final job state ('done' or 'failed'). Never raises for portal problems; they fail the job."""
    worker_id = worker_id or uuid.uuid4().hex
    job, ret, snap = gf.claim_job(db, job_id, worker_id)
    try:
        portal.open()
        portal.wait_for_login()
        shown = portal.taxpayer_gstin()
        if shown != snap.gstin:
            raise PortalError(f"The portal is signed in to a different taxpayer (GSTIN ending {str(shown)[-4:]}), "
                              f"not the one approved (ending {snap.gstin[-4:]}).")
        gf.checkpoint(db, job, worker_id, "taxpayer_confirmed", snap.gstin[-4:])
        portal.open_return(snap.return_type, snap.period)
        gf.checkpoint(db, job, worker_id, "period_opened", snap.period)
        if job.kind == "verify":
            st = portal.filing_status(snap.return_type, snap.period)
            ok = gf.record_verification(db, job, worker_id, st.get("status"), st.get("arn"), st.get("filed_at"))
            return "done" if ok else "failed"
        if snap.return_type == "GSTR1":
            _gstr1_upload(db, job, snap, portal, worker_id, sleep)
        else:
            if not job.checkpoints.get("values_saved"):
                portal.gstr3b_fill(snap.payload)
                gf.checkpoint(db, job, worker_id, "values_saved", True)
        totals = portal.summary_totals(snap.return_type)
        gf.checkpoint(db, job, worker_id, "totals_read", True)
        diffs = gf.finish_prepare(db, job, worker_id, totals)
        return "failed" if diffs else "done"
    except (PortalError, gf.FilingError) as exc:
        gf.fail_job(db, job, worker_id, getattr(exc, "message", None) or str(exc))
        return "failed"
    except Exception as exc:   # browser closed, network, timeouts: stop safely, nothing was filed
        log.warning("Portal job %s stopped: %s", job_id, type(exc).__name__)
        gf.fail_job(db, job, worker_id, f"The browser step did not complete ({type(exc).__name__}).")
        return "failed"
    finally:
        try:
            portal.close()
        except Exception:
            pass


def _gstr1_upload(db, job, snap, portal, worker_id, sleep) -> None:
    cps = job.checkpoints
    ref = (cps.get("upload_submitted") or {}).get("ref")
    if not ref and cps.get("upload_started"):
        # A previous run may have uploaded without saving the reference: adopt it instead of uploading again.
        found = portal.latest_upload(since=cps["upload_started"])
        if found:
            ref = found
            gf.checkpoint(db, job, worker_id, "upload_submitted", {"ref": ref, "adopted": True})
    if not ref:
        gf.checkpoint(db, job, worker_id, "upload_started", utcnow().isoformat())
        path = _write_payload(snap)
        try:
            ref = portal.gstr1_upload(path)
        finally:
            os.unlink(path)
        gf.checkpoint(db, job, worker_id, "upload_submitted", {"ref": ref})
    if not job.checkpoints.get("upload_processed"):
        waited = 0
        while True:
            status = portal.upload_status(ref)
            if status == "processed":
                break
            if status.startswith("error"):
                raise PortalError("The portal rejected some of the uploaded data. Download its error report from the "
                                  "upload history, correct the records in FinV and prepare the return again.")
            if waited >= UPLOAD_WAIT_S:
                raise PortalError("The portal is still processing the upload. Resume later; it will not be uploaded "
                                  "again.")
            sleep(UPLOAD_POLL_S)
            waited += UPLOAD_POLL_S
        gf.checkpoint(db, job, worker_id, "upload_processed", True)


def _write_payload(snap) -> str:
    """The upload file lives only for the upload, readable by this user only."""
    folder = settings.data_dir / "gst_uploads"
    folder.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f"{snap.return_type}_{snap.period}_v{snap.version}_", suffix=".json",
                                dir=folder)
    with os.fdopen(fd, "w") as f:
        json.dump(snap.payload, f, separators=(",", ":"))
    os.chmod(path, 0o600)
    return path
