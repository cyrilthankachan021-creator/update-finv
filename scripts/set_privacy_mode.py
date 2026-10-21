"""Set the privacy mode of every existing company, recording each change in its audit trail.

Usage (from the finv folder):  backend/.venv/bin/python scripts/set_privacy_mode.py cloud_ai
Then restart FinV:              systemctl --user restart finv-api
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_engine  # noqa: E402
from app.models import Company, CompanySettings  # noqa: E402
from app.services.audit_log import log_event  # noqa: E402

MODES = {"local_only", "pseudonymized_ai", "cloud_ai"}

if len(sys.argv) != 2 or sys.argv[1] not in MODES:
    sys.exit(f"Usage: set_privacy_mode.py {{{'|'.join(sorted(MODES))}}}")
mode = sys.argv[1]
init_engine()
db = SessionLocal()
changed = 0
for s in db.scalars(select(CompanySettings)).all():
    if s.privacy_mode != mode:
        log_event(db, s.company_id, None, "settings.changed", "company", s.company_id,
                  {"privacy_mode": {"from": s.privacy_mode, "to": mode}, "by": "set_privacy_mode.py"})
        print(f"{db.get(Company, s.company_id).name} (#{s.company_id}): {s.privacy_mode} -> {mode}")
        s.privacy_mode = mode
        changed += 1
db.commit()
print(f"{changed} companies changed.")
