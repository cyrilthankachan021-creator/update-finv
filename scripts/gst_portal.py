"""Run (or resume) a GST portal job in a visible browser. You sign in to the portal yourself.

Usage (from the finv folder):  backend/.venv/bin/python scripts/gst_portal.py <job id>
FinV shows the job id and this command on the GST Returns page. FinV never submits or files the return.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import SessionLocal, init_engine  # noqa: E402
from app.models import GstPortalJob  # noqa: E402
from app.portal.gst_portal import PlaywrightPortal  # noqa: E402
from app.portal.runner import run_job  # noqa: E402
from app.services.gst_filing import FilingError  # noqa: E402

if len(sys.argv) != 2 or not sys.argv[1].isdigit():
    sys.exit("Usage: gst_portal.py <job id>")
init_engine()
db = SessionLocal()
job_id = int(sys.argv[1])
print("A browser window will open. Sign in to the GST portal there yourself; FinV continues once you are signed in.")
try:
    state = run_job(db, job_id, PlaywrightPortal(headless=False))
except FilingError as exc:
    sys.exit(f"Not started: {exc.message}")
job = db.get(GstPortalJob, job_id)
db.refresh(job)
if state == "done":
    print("Done." if job.kind == "verify" else
          "Done. The data is on the portal and matches the approved figures. Review it on the portal and file it "
          "yourself; then click 'I have filed it' in FinV.")
else:
    print(f"Stopped: {job.error}\nNothing was filed. Fix the cause, then run this command again to resume.")
    if job.differences:
        for d in job.differences:
            print(f"  {d['field']}: FinV {d['finv']}, portal {d['portal']}")
    sys.exit(1)
