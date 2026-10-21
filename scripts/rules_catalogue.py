"""Print the rule catalogue (id, version, severity, title, purpose) straight from the engine registry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.engine import accounting, audit_checks, gst, reconciliation  # noqa: F401,E402  (register rules)
from app.engine.rules import rules_catalogue  # noqa: E402

print("| Rule | Version | Worst outcome | Check | What it does |")
print("|---|---|---|---|---|")
for r in rules_catalogue():
    print(f"| {r['id']} | {r['version']} | {r['severity']} | {r['title']} | {r['purpose']} |")
