"""Validation report for an imported bank statement: what was accepted, what was rejected and why, the period it
covers, and whether the statement's own running balance holds together. Pure functions; no database access."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .duplicates import bank_running_balance
from .money import ZERO

MAX_REJECTED_SHOWN = 200


def bank_report(records: list[dict], row_errors: list[dict]) -> dict:
    """records: extracted bank lines (dicts as produced by the ingestion pipeline). row_errors: {row, message}."""
    lines = sorted(records, key=lambda r: r.get("row_index") or 0)
    dates = [r["doc_date"] for r in lines if r.get("doc_date")]
    debits = sum((r.get("debit_amount") or ZERO for r in lines), ZERO)
    credits = sum((r.get("credit_amount") or ZERO for r in lines), ZERO)
    rows = [(r.get("row_index") or 0, r.get("debit_amount"), r.get("credit_amount"), r.get("balance")) for r in lines]
    has_balance = any(b is not None for *_, b in rows)
    breaks: list[dict] = []
    if has_balance:
        fwd, rev = bank_running_balance(rows), bank_running_balance(list(reversed(rows)))   # statements may be newest-first
        breaks = fwd if len(fwd) <= len(rev) else rev
    seen = Counter((r.get("doc_date"), r.get("debit_amount"), r.get("credit_amount"), (r.get("reference") or "").strip(),
                    (r.get("description") or "").strip()) for r in lines)
    repeated = sum(c - 1 for c in seen.values() if c > 1)
    checks = [
        {"check": "Every row could be read", "ok": not row_errors,
         "detail": "All rows were accepted." if not row_errors else f"{len(row_errors)} row(s) were rejected and left out."},
        {"check": "Dates found", "ok": bool(dates), "detail": f"{min(dates)} to {max(dates)}" if dates else "No dated lines."},
    ]
    if has_balance:
        checks.append({"check": "Running balance agrees", "ok": not breaks,
                       "detail": "Every line agrees with the previous balance." if not breaks
                       else f"{len(breaks)} line(s) do not agree, the first at row {breaks[0]['row']}."})
    else:
        checks.append({"check": "Running balance agrees", "ok": None, "detail": "The file has no balance column, so this could not be checked."})
    checks.append({"check": "No repeated lines", "ok": repeated == 0,
                   "detail": "No line appears twice." if not repeated else f"{repeated} line(s) repeat an earlier line exactly (same date, amount, reference and text)."})
    failed = [c for c in checks if c["ok"] is False]
    status = "failed" if not lines else "needs_attention" if failed else "passed"
    balances = [(i, b) for i, _, _, b in rows if b is not None]
    return {
        "status": status,
        "period_start": str(min(dates)) if dates else None, "period_end": str(max(dates)) if dates else None,
        "lines_accepted": len(lines), "lines_rejected": len(row_errors),
        "rejected": [{"row": e["row"], "message": e["message"]} for e in row_errors[:MAX_REJECTED_SHOWN]],
        "total_debits": str(debits), "total_credits": str(credits),
        "first_balance": str(balances[0][1]) if balances else None, "last_balance": str(balances[-1][1]) if balances else None,
        "balance_breaks": [{k: b[k] for k in ("row", "expected", "stated")} for b in breaks[:20]],
        "repeated_lines": repeated, "checks": checks,
    }
