"""Fast, exact answers for common requests, without calling the model.

Every answer here is built from FinV's own data, so it is instant (well under a second) and cannot be invented.
Changes are still only *prepared* (Confirm required).
"""
from __future__ import annotations

import re

from sqlalchemy import func, select

from ..models import AuditException, DuplicateCandidate, JournalEntry, SourceRecord

STATUS_WORDS = [
    (r"pending|need(?:s|ing)? review|to review|waiting|open entries", "needs_review", "need review"),
    (r"validated|ready to approve|passed", "validated", "are validated"),
    (r"approved", "approved", "are approved"),
    (r"posted", "posted", "are posted"),
    (r"rejected", "rejected", "are rejected"),
    (r"issues?|errors?|problems?|failing", "issues", "have errors"),
]
STOP = {"the", "line", "entry", "bank", "set", "use", "change", "classify", "mark", "book", "put", "to", "as", "under",
        "into", "for", "this", "that", "please", "ledger", "account", "is", "a", "an", "it", "of", "on", "in", "and",
        "with", "charge", "charges", "transaction", "payment", "receipt", "debit", "credit"}


def _name(ctx) -> str:
    from ..models import User
    u = ctx.db.get(User, ctx.user_id)
    return (u.name.split()[0] if u and u.name else "").strip()


def _counts(ctx) -> dict:
    from . import agent
    return agent.t_overview(ctx)


def _status_line(ctx) -> str:
    if ctx.workspace == "audit":
        by = _counts(ctx)["exceptions_by_status"]
        open_ = sum(by.get(s, 0) for s in ("open", "needs_information", "under_review"))
        return f"{open_} audit exception{'s' if open_ != 1 else ''} {'are' if open_ != 1 else 'is'} unresolved."
    c = _counts(ctx)
    e = c["entries_by_status"]
    parts = [f"{e.get('needs_review', 0)} need review", f"{e.get('validated', 0)} validated",
             f"{e.get('approved', 0)} approved", f"{e.get('posted', 0)} posted"]
    d = c["open_possible_duplicates"]
    return "Entries: " + ", ".join(parts) + f". Possible duplicates: {d}."


def route(ctx, text: str) -> str | None:
    """Return an exact answer, or None to let the model handle it."""
    from . import agent
    t = re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")

    # greetings / thanks / help
    if re.fullmatch(r"(hi|hello|hey|hii+|helo|hlo|hllo|hai|namaste|namaskar|good (morning|afternoon|evening)|hello there|hi there)( finv)?", t):
        n = _name(ctx)
        return (f"Hello{(' ' + n) if n else ''}! {_status_line(ctx)}\n"
                + ("Try: “what needs review?”, “show entry 12”, “set entry 12 to Bank Charges”, “go to duplicates”."
                   if ctx.workspace == "accounting" else
                   "Try: “show open exceptions”, “go to trial balance”, “go to vouching”."))
    if re.fullmatch(r"(thanks|thank you|thank u|thx|ok thanks|great|cool|dhanyavad|shukriya)( so much)?", t):
        return "You're welcome."
    if re.fullmatch(r"(help|what can you do|commands|how do i use (this|you)|what do you do)", t):
        return agent.HELP

    # summary / status
    if re.search(r"\b(summary|status|overview|situation|how are (we|things)|where do we stand)\b", t) and not t.startswith(("go to", "open")):
        return _status_line(ctx)

    if ctx.workspace == "audit":
        if re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", t) and "exception" in t:
            return _status_line(ctx)
        return None

    # counts
    if re.search(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b", t):
        if "duplicate" in t:
            d = _counts(ctx)["open_possible_duplicates"]
            return f"There {'is' if d == 1 else 'are'} {d} open possible duplicate{'s' if d != 1 else ''}."
        for pat, status, words in STATUS_WORDS:
            if re.search(pat, t):
                n = agent.t_find_entries(ctx, status, limit=200)["count"] if status == "issues" else \
                    _counts(ctx)["entries_by_status"].get(status, 0)
                return f"{n} entr{'y' if n == 1 else 'ies'} {words}."
        if "entr" in t:
            return _status_line(ctx)

    # bank lines that still need a ledger
    if "bank" in t and re.search(r"ledger|classif|categor|need|pending|unknown|missing", t) and re.search(r"which|show|list|what|any", t):
        rows = ctx.db.scalars(select(JournalEntry).join(SourceRecord, JournalEntry.source_record_id == SourceRecord.id).where(
            JournalEntry.company_id == ctx.company.id, JournalEntry.status == "needs_review",
            SourceRecord.record_type == "bank_txn")).all()
        rows = [e for e in rows if not e.lines]
        ctx.tools_used.append("find_entries")
        if not rows:
            return "Every bank line has a ledger."
        lines = [f"#{e.id} {e.narration} ({agent._entry_brief(ctx, e)['amount']})" for e in rows[:15]]
        return (f"{len(rows)} bank line{'s' if len(rows) != 1 else ''} still need{'' if len(rows) != 1 else 's'} a ledger:\n" + "\n".join(lines)
                + "\nSay for example “set entry " + str(rows[0].id) + " to Bank Charges”.")

    # questions about one entry: "entry 104", "why does the Kaveri invoice need review", "explain the sms line"
    num = re.search(r"\b(?:entry|entries|#)\s*#?(\d+)\b", t)
    about = re.search(r"\b(why|explain|what|which|tell me about|details?|show|status of|problem with|wrong with)\b", t)
    if num and about and not re.match(r"^(approve|post|reject|set|use|change)\b", t):
        return describe_entry(ctx, agent._entry(ctx, num.group(1)))
    if re.search(r"\b(why|explain|what'?s wrong|problem|flagged|need(s)? review|error)\b", t) and not num:
        best, hits = find_entry_by_words(ctx, t, None)
        if best is not None:
            return describe_entry(ctx, best)
        if hits:
            return "Which one do you mean? " + ", ".join(f"#{e.id} {e.narration}" for e in hits[:6])

    # lists by status
    if re.search(r"^(show|list|which|what|give|display|any)\b", t) and re.search(r"entr|review|pending|validated|approved|posted|rejected|issues?|errors?", t):
        for pat, status, words in STATUS_WORDS:
            if re.search(pat, t):
                r = agent.t_find_entries(ctx, status, limit=10)
                ctx.tools_used.append("find_entries")
                if not r["count"]:
                    return f"No entries {words}."
                lines = [f"#{e['entry_id']} {e['narration']} ({e['amount']})" + (f" — {e['question']}" if e["question"] else "")
                         for e in r["entries"]]
                return f"Entries that {words}:\n" + "\n".join(lines)

    # reconciliation
    if re.search(r"reconcil|unmatched", t) and not t.startswith(("go to", "open")):
        r = agent.t_reconciliation(ctx)
        ctx.tools_used.append("reconciliation")
        return (f"Bank reconciliation: {r['matched']} matched, {r['unmatched_bank_lines']} bank lines unmatched, "
                f"{r['unmatched_entries']} entries without a bank line, {r['need_manual_match']} need a manual match.")

    # natural-language ledger change: "set entry 12 to bank charges", "mark the sms charges line as bank charges"
    m = re.search(r"\b(?:set|use|change|classify|mark|book|put|move|post it)\b(.*?)\b(?:to|as|under|into|in)\s+(?:the\s+)?(?:ledger\s+)?(.+?)(?:\s+ledger|\s+account)?$", t)
    if m:
        target, ledger_ref = m.group(1), m.group(2).strip()
        acct = agent._resolve_ledger(ctx, ledger_ref)
        if acct is None:
            return None  # let the model try (or ask) — the ledger phrase is unclear
        num = re.search(r"#?(\d+)", target)
        if num:
            entry_id = num.group(1)
        else:
            best, hits = find_entry_by_words(ctx, target, ["needs_review", "validated"])
            if best is None:
                if not hits:
                    return None
                return ("Several entries match: " + ", ".join(f"#{e.id} {e.narration}" for e in hits[:6])
                        + f". Say for example “set entry {hits[0].id} to {acct.name}”.")
            entry_id = best.id
        r = agent.t_set_ledger(ctx, entry_id, acct.code)
        ctx.tools_used.append("set_ledger")
        return r.get("error") or f"{r['summary']} Press Confirm to go ahead."
    return None


def open_duplicates(ctx) -> int:
    return ctx.db.scalar(select(func.count()).select_from(DuplicateCandidate).where(
        DuplicateCandidate.company_id == ctx.company.id, DuplicateCandidate.status == "open"))


def open_exceptions(ctx) -> int:
    return ctx.db.scalar(select(func.count()).select_from(AuditException).where(
        AuditException.company_id == ctx.company.id,
        AuditException.status.in_(["open", "needs_information", "under_review"])))


QUESTION_WORDS = {"why", "does", "did", "need", "needs", "review", "explain", "what", "whats", "wrong", "problem", "flagged",
                  "error", "errors", "invoice", "bill", "show", "tell", "about", "details", "detail", "status", "with", "have",
                  "has", "there", "which", "how", "line", "entry", "the", "was", "were", "not", "being"}


def find_entry_by_words(ctx, text: str, statuses: list[str] | None):
    """Entry whose narration matches the most meaningful words of the request. Returns (best or None, candidates)."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP and w not in QUESTION_WORDS}
    if not words:
        return None, []
    stmt = select(JournalEntry).where(JournalEntry.company_id == ctx.company.id)
    if statuses:
        stmt = stmt.where(JournalEntry.status.in_(statuses))
    else:
        stmt = stmt.where(JournalEntry.status != "rejected")
    scored = []
    for e in ctx.db.scalars(stmt).all():
        hay = set(re.findall(r"[a-z0-9]{3,}", (e.narration or "").lower()))
        n = len(words & hay)
        if n:
            scored.append((n, e))
    if not scored:
        return None, []
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    top = scored[0][0]
    best = [e for n, e in scored if n == top]
    if len(best) == 1 and (top >= 2 or len(scored) == 1 or top == len(words)):
        return best[0], [e for _, e in scored]
    return None, [e for _, e in scored]


def describe_entry(ctx, e) -> str:
    from . import agent
    ctx.tools_used.append("get_entry")
    if e.source_record_id:
        ctx.navigate = f"/accounting/transactions/{e.source_record_id}"
    d = agent.t_get_entry(ctx, e.id)
    out = [f"Entry #{e.id}: {e.narration} — {d['amount']}, status {d['status'].replace('_', ' ')}."]
    if d["lines"]:
        out.append("Booked: " + "; ".join(f"{l['ledger']} {'Dr ' + l['debit'] if l['debit'] else 'Cr ' + l['credit']}"
                                          for l in d["lines"]) + ".")
    else:
        out.append("No ledgers have been chosen yet.")
    if d["question"]:
        out.append("Open question: " + d["question"])
    if d["errors"]:
        out.append("Errors: " + " ".join(d["errors"]))
    if d["review_points"] and d["review_points"] != [d["question"]]:
        out.append("To review: " + " ".join(p for p in d["review_points"] if p != d["question"]))
    if d.get("uncertainties"):
        out.append("Why: " + " ".join(d["uncertainties"][:2]))
    if d["status"] == "validated":
        out.append(f"All checks passed. Say “approve entry {e.id}” to approve it.")
    return "\n".join(out)
