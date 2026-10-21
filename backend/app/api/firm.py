"""Firm-wide overview for the CA dashboard: compliance tasks, issues and imports across every client the user can see.

Everything is computed from stored records at request time; nothing here is cached or invented. Each client is only
included when the signed-in user is a member of its organization, so the firm view cannot leak another firm's data.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Account, AuditEvent, AuditException, BankMatch, Company, CompanySettings, Document, DuplicateCandidate, GstReturn, JournalEntry,
    JournalLine, Membership, SourceRecord, TrialBalanceImport, User, ValidationResult, utcnow,
)
from ..security import CompanyAccess, company_access, current_user, visible_to
from ..services.audit_log import log_event
from . import serializers as S

router = APIRouter(prefix="/api/firm", tags=["firm"])

# Statutory monthly due days (day of the month after the tax period). Quarterly (QRMP) filers are not modelled.
GST_DUE_DAY = {"GSTR1": 11, "GSTR3B": 20}
DUE_SOON_DAYS = 7
# GST return state -> (task status, recommended next action)
GST_STATES = {
    "prepared": ("prepared", "Send the prepared return for approval."),
    "awaiting_approval": ("awaiting_review", "Review and approve the return."),
    "approved": ("in_progress", "Start portal preparation."),
    "portal_in_progress": ("in_progress", "Wait for portal preparation to finish."),
    "portal_ready_for_review": ("awaiting_review", "Check the portal figures, then file on the GST portal."),
    "portal_failed": ("error", "Portal preparation failed. Open the return and retry."),
    "invalidated": ("error", "Source data changed after preparation. Prepare the return again."),
    "filing_reported": ("completed", "Verify the filing on the portal."),
    "verified": ("verified", "Nothing to do."),
    "cancelled": ("not_started", "Prepare the return."),
}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def financial_year(d: date) -> str:
    """Indian financial year (April to March) containing d, e.g. date(2026, 10, 1) -> "2026-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _months_back(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _due(period: str, rtype: str) -> date:
    y, m = int(period[:4]), int(period[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, GST_DUE_DAY[rtype])


# QRMP: the quarterly GSTR-3B is due on the 22nd in these states and union territories, the 24th elsewhere (by GSTIN state code).
QRMP_22_STATES = {"22", "23", "24", "25", "26", "27", "29", "30", "31", "32", "33", "34", "35", "36", "37"}
QRMP_QUARTER_END_MONTHS = {3, 6, 9, 12}
TDS_DEPOSIT_DAY = 7        # TDS deducted in a month is deposited by the 7th of the next month


def _qrmp_due(period: str, rtype: str, gstin: str) -> date:
    y, m = int(period[:4]), int(period[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, 13 if rtype == "GSTR1" else 22 if gstin[:2] in QRMP_22_STATES else 24)


def _urgency(due: date, today: date, done: bool) -> str:
    if done:
        return "done"
    if due < today:
        return "overdue"
    return "due_soon" if (due - today).days <= DUE_SOON_DAYS else "upcoming"


def _gst_tasks(db: Session, companies: list[Company], today: date) -> list[dict]:
    existing: dict[tuple[int, str, str], GstReturn] = {}
    ids = [c.id for c in companies]
    if ids:
        for r in db.scalars(select(GstReturn).where(GstReturn.company_id.in_(ids))):
            existing[(r.company_id, r.return_type, r.period)] = r
    tasks = []
    qrmp = {x.company_id for x in db.scalars(select(CompanySettings).where(CompanySettings.company_id.in_(ids), CompanySettings.gst_filing == "qrmp"))} if ids else set()
    for c in companies:
        if not c.gstin or "accounting" not in (c.services or ["accounting", "audit"]):
            continue        # GST work only applies to registered clients the firm does accounting for
        first = date(c.created_at.year, c.created_at.month, 1)
        for back in range(0, 6):
            ps = _months_back(today, back)
            period = f"{ps.year}-{ps.month:02d}"
            if c.id in qrmp and ps.month not in QRMP_QUARTER_END_MONTHS:
                continue        # quarterly filers file for the quarter, in the month after it ends
            for rtype in GST_DUE_DAY:
                due = _qrmp_due(period, rtype, c.gstin) if c.id in qrmp else _due(period, rtype)
                ret = existing.get((c.id, rtype, period))
                if ret is None and (ps < first or due < today - timedelta(days=120) or due > today + timedelta(days=45)):
                    continue        # not before the client joined FinV; not far past or far ahead
                status, action = GST_STATES.get(ret.state, ("prepared", "Open the return.")) if ret else ("not_started", "Prepare the return.")
                done = status in {"completed", "verified"}
                urgency = _urgency(due, today, done)
                tasks.append({
                    "key": f"gst:{c.id}:{rtype}:{period}", "client_id": c.id, "client_name": c.name, "gstin": c.gstin,
                    "category": "gst", "task": f"{'GSTR-1' if rtype == 'GSTR1' else 'GSTR-3B'} return" + (" (quarter)" if c.id in qrmp else ""),
                    "period": period, "financial_year": financial_year(ps), "due_date": due.isoformat(), "status": status, "urgency": urgency,
                    "severity": "high" if urgency == "overdue" or status == "error" else "medium" if urgency == "due_soon" else "low",
                    "issue": ret.state_reason if ret and status == "error" else
                             ("Not started" if status == "not_started" else None),
                    "next_action": action + (" Quarterly filer: prepare each month of the quarter." if c.id in qrmp and not ret else ""),
                    "link": f"/accounting/gst/{ret.id}" if ret else "/accounting/gst",
                })
    return tasks


def tds_position(db: Session, company_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    """Per company: TDS deducted per month and the balance still payable, from posted FinV entries only."""
    out: dict[int, dict] = {}
    if not company_ids:
        return out
    rows = db.execute(select(JournalEntry.company_id, JournalEntry.entry_date, JournalLine.credit, JournalLine.debit)
                      .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
                      .join(Account, Account.id == JournalLine.account_id)
                      .where(JournalEntry.company_id.in_(company_ids), JournalEntry.status == "posted", Account.role == "tds_payable")).all()
    for cid, d, cr, dr in rows:
        o = out.setdefault(cid, {"deducted": {}, "payable": Decimal("0")})
        o["payable"] += (cr or 0) - (dr or 0)
        if d and cr:
            k = f"{d.year}-{d.month:02d}"
            o["deducted"][k] = o["deducted"].get(k, Decimal("0")) + cr
    return out


def _tds_tasks(db: Session, companies: list[Company], today: date) -> list[dict]:
    on = {x.company_id: x for x in db.scalars(select(CompanySettings).where(
        CompanySettings.company_id.in_([c.id for c in companies]), CompanySettings.tds_applicable.is_(True)))} if companies else {}
    pos = tds_position(db, list(on))
    tasks = []
    for c in companies:
        if c.id not in on:
            continue
        p = pos.get(c.id, {"deducted": {}, "payable": Decimal("0")})
        for period, amount in sorted(p["deducted"].items())[-6:]:
            y, m = int(period[:4]), int(period[5:7])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            due = date(y, m, TDS_DEPOSIT_DAY)
            done = p["payable"] <= 0                  # nothing left payable: the deductions have been paid
            urgency = _urgency(due, today, done)
            if due < today - timedelta(days=120) and done:
                continue
            tasks.append({
                "key": f"tds:{c.id}:{period}", "client_id": c.id, "client_name": c.name, "gstin": c.gstin, "category": "tds",
                "task": "TDS deposit", "period": period, "financial_year": financial_year(date(int(period[:4]), int(period[5:7]), 1)),
                "due_date": due.isoformat(), "status": "completed" if done else "pending", "urgency": urgency,
                "severity": "high" if urgency == "overdue" else "medium" if urgency == "due_soon" else "low",
                "issue": None if done else f"TDS deducted {amount}; {p['payable']} still payable according to posted entries.",
                "next_action": "No action." if done else "Deposit the TDS and record the payment.", "link": "/accounting/entries",
            })
    return tasks


def _issues(db: Session, companies: list[Company]) -> list[dict]:
    names = {c.id: c.name for c in companies}
    out: list[dict] = []

    def add(c_id: int, **kw):
        out.append({"client_id": c_id, "client_name": names[c_id], "financial_year": None, **kw})

    ids = list(names)
    if not ids:
        return out
    # Audit exceptions: produced by deterministic audit rules; a reviewer still has to confirm or resolve them.
    exs = db.scalars(select(AuditException).where(AuditException.company_id.in_(ids),
                                                 AuditException.status.in_(["open", "needs_information", "under_review"]))).all()
    imps = {i.id: i for i in db.scalars(select(TrialBalanceImport).where(
        TrialBalanceImport.id.in_({x.import_id for x in exs if x.import_id})))} if exs else {}
    for x in exs:
        imp = imps.get(x.import_id)
        ev = x.evidence or {}
        expected = actual = None
        if "expected" in ev and "closing" in ev:                    # opening + movement should equal closing
            expected, actual = str(ev["expected"]), str(ev["closing"])
        elif "ledger" in ev and "tb" in ev:                         # ledger totals against the trial balance
            expected, actual = "Dr {} / Cr {}".format(*ev["tb"]), "Dr {} / Cr {}".format(*ev["ledger"])
        add(x.company_id, financial_year=financial_year(imp.period_start) if imp and imp.period_start else None,
            period=(imp.period_label if imp else None), expected=expected, actual=actual, evidence=ev,
            source={"import_id": x.import_id, "document_id": imp.document_id if imp else None},
            key=f"audit:{x.id}", category="audit_exception", severity=x.severity, title=x.title,
            description=x.explanation, detection="Audit rule " + x.rule_id, nature="rule_detected",
            review_status=x.status, next_action=x.suggested_action or "Investigate and record a conclusion.",
            link=f"/audit/exceptions/{x.id}", created_at=S.d(x.created_at))
    # Entries whose latest validation failed.
    err = db.execute(select(JournalEntry.company_id, func.count(func.distinct(JournalEntry.id))).join(
        ValidationResult, (ValidationResult.entry_id == JournalEntry.id) & (ValidationResult.entry_version == JournalEntry.version))
        .where(JournalEntry.company_id.in_(ids), JournalEntry.status.in_(["needs_review", "validated"]),
               ValidationResult.status == "error").group_by(JournalEntry.company_id)).all()
    for cid, n in err:
        add(cid, key=f"validation:{cid}", category="validation_error", severity="high",
            title=f"{n} entr{'y' if n == 1 else 'ies'} failed validation", description="Accounting or GST rules returned an error; these cannot be approved until corrected.",
            detection="Accounting rules", nature="rule_detected", review_status="open", count=n,
            next_action="Open Entries and correct the failing items.", link="/accounting/entries", created_at=None)
    dups = db.execute(select(DuplicateCandidate.company_id, func.count()).where(
        DuplicateCandidate.company_id.in_(ids), DuplicateCandidate.status == "open").group_by(DuplicateCandidate.company_id)).all()
    for cid, n in dups:
        add(cid, key=f"duplicates:{cid}", category="possible_duplicate", severity="medium",
            title=f"{n} possible duplicate{'' if n == 1 else 's'} awaiting a decision",
            description="Records look the same or nearly the same as earlier ones. This is a suspicion until a person decides.",
            detection="Duplicate detection", nature="potential", review_status="open", count=n,
            next_action="Decide whether each pair is a duplicate.", link="/accounting/duplicates", created_at=None)
    # Bank lines nobody has confirmed a match for yet. Cheap to count for every client; the exact reconciliation
    # (auto-matches, near misses) is computed only when a client's Bank Reconciliation page is opened.
    matched = select(BankMatch.bank_record_id).where(BankMatch.status == "confirmed")
    bank = db.execute(select(SourceRecord.company_id, func.count()).where(
        SourceRecord.company_id.in_(ids), SourceRecord.record_type == "bank_txn", SourceRecord.status != "duplicate",
        SourceRecord.id.notin_(matched)).group_by(SourceRecord.company_id)).all()
    for cid, n in bank:
        add(cid, key=f"bank:{cid}", category="bank_reconciliation", severity="medium",
            title=f"{n} bank line{'' if n == 1 else 's'} without a confirmed match",
            description="Imported bank statement lines that have not been matched to a book entry and confirmed.",
            detection="Bank statement import", nature="potential", review_status="open", count=n,
            next_action="Open Bank Reconciliation and match or explain each line.",
            link="/accounting/bank", created_at=None)
    # Missing data: a client with no usable import at all.
    have = {r[0] for r in db.execute(select(Document.company_id).where(Document.company_id.in_(ids), Document.status == "processed").distinct())}
    have |= {r[0] for r in db.execute(select(TrialBalanceImport.company_id).where(TrialBalanceImport.company_id.in_(ids)).distinct())}
    for c in companies:
        if c.id not in have:
            add(c.id, key=f"nodata:{c.id}", category="missing_data", severity="medium", title="No accounting data imported yet",
                description="No bank statement, register, trial balance or ledger has been imported for this client.",
                detection="Import check", nature="missing_data", review_status="open", next_action="Import a bank statement or Tally export.",
                link="/imports", created_at=S.d(c.created_at))
    out.sort(key=lambda i: (SEVERITY_RANK.get(i["severity"], 9), i["client_name"], i["key"]))
    return out


def _scope(db: Session, user: User) -> tuple[list[Company], dict[int, str]]:
    """The clients this user may see: those of every organization they belong to. Nothing else is ever queried."""
    roles = {m.organization_id: m.role for m in db.scalars(select(Membership).where(Membership.user_id == user.id))}
    companies = db.scalars(select(Company).where(Company.organization_id.in_(roles)).order_by(Company.name)).all() if roles else []
    return visible_to(db, user.id, list(companies), roles), roles


# A firm with hundreds of clients recomputes its whole overview on every request, so such firms get a short cache.
# Smaller firms are always computed fresh: their numbers must reflect the last action.
BIG_FIRM_CLIENTS = 200
CACHE_SECONDS = 20
_cache: dict[tuple[int, int | None], tuple[float, tuple]] = {}


def _compute(db: Session, user: User, client_id: int | None = None):
    import time
    hit = _cache.get((user.id, client_id))
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    result = _compute_fresh(db, user, client_id)
    if client_id is None and len(result[4]) >= BIG_FIRM_CLIENTS:
        _cache[(user.id, client_id)] = (time.monotonic(), result)
        if len(_cache) > 500:
            _cache.clear()
    return result


def _compute_fresh(db: Session, user: User, client_id: int | None = None):
    today = date.today()
    companies, roles = _scope(db, user)
    if client_id is not None:
        companies = [c for c in companies if c.id == client_id]
        if not companies:
            raise HTTPException(404, "Company not found.")
    ids = [c.id for c in companies]
    tasks = _gst_tasks(db, companies, today) + _tds_tasks(db, companies, today)
    review = dict(db.execute(select(JournalEntry.company_id, func.count()).where(
        JournalEntry.company_id.in_(ids), JournalEntry.status == "needs_review").group_by(JournalEntry.company_id)).all()) if ids else {}
    for c in companies:
        if review.get(c.id):
            n = review[c.id]
            tasks.append({"key": f"review:{c.id}", "client_id": c.id, "client_name": c.name, "gstin": c.gstin, "category": "entry_review",
                          "financial_year": financial_year(today),
                          "task": f"Review {n} accounting entr{'y' if n == 1 else 'ies'}", "period": None, "due_date": None,
                          "status": "awaiting_review", "urgency": "upcoming", "severity": "low", "issue": None,
                          "next_action": "Review, correct and approve the entries.", "link": "/accounting/entries"})
    issues = _issues(db, companies)
    for i in issues:                    # a client with nothing imported also has a task: the work is to get the documents
        if i["category"] == "missing_data":
            tasks.append({"key": f"missing:{i['client_id']}", "client_id": i["client_id"], "client_name": i["client_name"], "gstin": next((c.gstin for c in companies if c.id == i["client_id"]), None),
                          "category": "missing_data", "financial_year": financial_year(today), "task": "Upload accounting data", "period": None, "due_date": None,
                          "status": "missing_documents", "urgency": "upcoming", "severity": "medium", "issue": i["title"],
                          "next_action": "Import a bank statement or Tally export.", "link": "/imports"})
    last_import = dict(db.execute(select(Document.company_id, func.max(Document.created_at)).where(
        Document.company_id.in_(ids), Document.status == "processed").group_by(Document.company_id)).all()) if ids else {}
    stats: dict[int, dict] = {c.id: {"overdue": 0, "due_soon": 0, "pending_tasks": 0, "awaiting_review": 0, "issues": 0,
                                      "high_issues": 0, "missing_data": False} for c in companies}
    for t in tasks:
        row = stats[t["client_id"]]
        if t["status"] not in {"completed", "verified"}:
            row["pending_tasks"] += 1
        if t["urgency"] == "overdue":
            row["overdue"] += 1
        elif t["urgency"] == "due_soon":
            row["due_soon"] += 1
        if t["status"] == "awaiting_review":
            row["awaiting_review"] += 1
    for i in issues:
        row = stats[i["client_id"]]
        row["issues"] += 1
        row["high_issues"] += i["severity"] == "high"
        row["missing_data"] |= i["category"] == "missing_data"
    clients = []
    for c in companies:
        row = stats[c.id]
        work = ("overdue" if row["overdue"] else "needs_attention" if row["high_issues"] or row["awaiting_review"] or row["due_soon"]
                else "missing_data" if row["missing_data"] else "on_track")
        clients.append({**S.company(c, roles[c.organization_id]), **row, "financial_year": financial_year(today), "work_status": work,
                        "last_import_at": S.d(last_import.get(c.id))})
    return today, companies, tasks, issues, clients


def _page(items: list, page: int, size: int) -> dict:
    return {"items": items[(page - 1) * size: page * size], "total": len(items), "page": page, "page_size": size}


def _has(q: str, *parts) -> bool:
    return q.lower() in " ".join(str(p or "") for p in parts).lower()


@router.get("/overview")
def firm_overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Counts and the few most urgent items only. Full lists are paginated under /tasks, /issues, /clients and /imports."""
    today, companies, tasks, issues, clients = _compute(db, user)
    open_tasks = [t for t in tasks if t["status"] not in {"completed", "verified"}]
    attention = [t for t in tasks if t["urgency"] in {"overdue", "due_soon"} or t["status"] in {"error", "awaiting_review"}]
    return {
        "as_of": today.isoformat(), "financial_year": financial_year(today),
        "summary": {
            "clients": len(companies),
            "gst_pending": sum(1 for t in open_tasks if t["category"] == "gst"),
            "gst_overdue": sum(1 for t in open_tasks if t["category"] == "gst" and t["urgency"] == "overdue"),
            "awaiting_review": sum(1 for t in open_tasks if t["status"] == "awaiting_review"),
            "due_soon": sum(1 for t in open_tasks if t["urgency"] == "due_soon"),
            "issues_open": len(issues), "issues_high": sum(1 for i in issues if i["severity"] == "high"),
            "audit_findings": sum(1 for i in issues if i["category"] == "audit_exception"),
            "missing_data": sum(1 for i in issues if i["category"] == "missing_data"),
        },
        "attention": attention[:8], "top_issues": issues[:8],
        # What this view cannot see yet, so the dashboard never implies it is covered.
        "not_tracked": [
            {"area": "TDS returns and challans", "reason": "TDS deposits are tracked only from posted FinV entries for clients marked TDS applicable; challans and quarterly returns (24Q/26Q) are not."},
            {"area": "Clients not set up for GST filing frequency", "reason": "Clients default to monthly filing until their GST filing frequency is set in Settings."},
            {"area": "GSTR-2B reconciliation", "reason": "GSTR-2B data is not imported yet."},
        ],
    }


CLIENT_FILTERS = {
    "overdue": lambda c: c["overdue"] > 0, "high_issues": lambda c: c["high_issues"] > 0,
    "pending_review": lambda c: c["awaiting_review"] > 0, "missing_data": lambda c: c["missing_data"],
    "due_soon": lambda c: c["due_soon"] > 0,
}
CLIENT_SORTS = {
    "name": lambda c: c["name"].lower(), "overdue": lambda c: (-c["overdue"], c["name"].lower()),
    "issues": lambda c: (-c["issues"], c["name"].lower()), "last_import": lambda c: (c["last_import_at"] is not None, c["last_import_at"] or "", c["name"].lower()),
}


@router.get("/clients")
def firm_clients(q: str = "", filter: str = "", sort: str = "name", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    clients = _compute(db, user)[4]
    if filter in CLIENT_FILTERS:
        clients = [c for c in clients if CLIENT_FILTERS[filter](c)]
    if q:
        clients = [c for c in clients if _has(q, c["name"], c["gstin"], c["id"])]
    clients.sort(key=CLIENT_SORTS.get(sort, CLIENT_SORTS["name"]))
    return _page(clients, page, page_size)


@router.get("/clients/{company_id}")
def client_summary(company_id: int, acc: CompanyAccess = Depends(company_access), user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """One client's workspace header and its own tasks and findings. Authorised through the same per-company check as every
    other client route, so another firm's client id is a 404."""
    today, _, tasks, issues, clients = _compute(db, user, acc.company.id)
    recent = db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.company_id == company_id, AuditEvent.user_id == user.id, AuditEvent.action == "client.opened",
        AuditEvent.created_at >= utcnow() - timedelta(minutes=10)))
    if not recent:                      # who opened a client's records, once per visit rather than once per refresh
        log_event(db, company_id, user.id, "client.opened", "company", company_id, {})
        db.commit()
    st = db.get(CompanySettings, company_id)
    pos = tds_position(db, [company_id]).get(company_id)
    tds = {"applicable": bool(st and st.tds_applicable), "tan": st.tan if st else None, "gst_filing": (st.gst_filing if st else None) or "monthly",
           "payable": str(pos["payable"]) if pos else "0"}
    return {"client": clients[0], "tasks": tasks, "issues": issues, "tds": tds,
            "not_tracked": ["TDS returns and challans are not tracked."]}


@router.get("/tasks")
def firm_tasks(client_id: int | None = None, category: str = "", financial_year: str = "", status: str = "", q: str = "",
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    tasks = _compute(db, user, client_id)[2]
    facets = {"financial_years": sorted({t["financial_year"] for t in tasks if t["financial_year"]}, reverse=True),
              "categories": sorted({t["category"] for t in tasks})}
    f = TASK_FILTERS.get(status)
    rows = [t for t in tasks if (not category or t["category"] == category) and (not financial_year or t["financial_year"] == financial_year)
            and (f is None or f(t)) and (not q or _has(q, t["client_name"], t["gstin"], t["task"], t["period"]))]
    return {**_page(rows, page, page_size), "facets": facets}


TASK_FILTERS = {
    "overdue": lambda t: t["urgency"] == "overdue", "due_soon": lambda t: t["urgency"] == "due_soon",
    "pending": lambda t: t["status"] in {"not_started", "prepared", "in_progress"}, "error": lambda t: t["status"] == "error",
    "awaiting_review": lambda t: t["status"] == "awaiting_review", "completed": lambda t: t["urgency"] == "done",
    "missing_documents": lambda t: t["status"] == "missing_documents",
}


@router.get("/issues")
def firm_issues(client_id: int | None = None, category: str = "", severity: str = "", status: str = "", financial_year: str = "",
                period: str = "", q: str = "", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    issues = _compute(db, user, client_id)[3]
    facets = {"categories": sorted({i["category"] for i in issues}), "statuses": sorted({i["review_status"] for i in issues})}
    rows = [i for i in issues if (not category or i["category"] == category) and (not severity or i["severity"] == severity)
            and (not status or i["review_status"] == status) and (not financial_year or i["financial_year"] == financial_year)
            and (not period or period.lower() in (i.get("period") or "").lower())
            and (not q or _has(q, i["client_name"], i["title"], i["description"]))]
    return {**_page(rows, page, page_size), "facets": facets}


@router.get("/imports")
def firm_imports(client_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    companies, _ = _scope(db, user)
    names = {c.id: c.name for c in companies}
    if client_id is not None and client_id not in names:
        raise HTTPException(404, "Company not found.")
    ids = [client_id] if client_id is not None else list(names)
    if not ids:
        return _page([], page, page_size)
    q = select(Document).where(Document.company_id.in_(ids))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    docs = db.scalars(q.order_by(Document.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{**S.document(d), "client_id": d.company_id, "client_name": names[d.company_id]} for d in docs],
            "total": total, "page": page, "page_size": page_size}
