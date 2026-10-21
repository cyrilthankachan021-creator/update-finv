"""Open the GST portal browser window for a job from the FinV server, on this computer's screen.

The window runs scripts/gst_portal.py in its own process, so it keeps going if the page is closed. Starting it twice
is harmless: the job lease lets only one window work on a job. If there is no screen (e.g. FinV runs on a server),
nothing is launched and the page shows the command to run instead.
"""
from __future__ import annotations

import logging
import os
import subprocess

from ..config import BACKEND_DIR, settings

log = logging.getLogger("finv.portal")
PYTHON = BACKEND_DIR / ".venv/bin/python"
SCRIPT = BACKEND_DIR.parent / "scripts/gst_portal.py"


def can_launch() -> bool:
    return settings.gst_open_browser and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def launch(job_id: int) -> bool:
    if not can_launch():
        return False
    folder = settings.data_dir / "gst_jobs"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with open(folder / f"job_{job_id}.log", "a") as out:
            subprocess.Popen([str(PYTHON), str(SCRIPT), str(job_id)], stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(BACKEND_DIR.parent))
    except OSError as exc:
        log.warning("Could not open the portal window for job %s: %s", job_id, type(exc).__name__)
        return False
    return True
