"""Trial balance computed from a client's own posted entries."""
from datetime import date

from app.db import SessionLocal
from app.models import Account, JournalEntry, JournalLine


def post(cid, d, lines, status="posted"):
    db = SessionLocal()
    try:
        accts = {a.code: a for a in db.query(Account).filter_by(company_id=cid)}
        e = JournalEntry(company_id=cid, entry_date=d, status=status)
        db.add(e)
        db.flush()
        for code, dr, cr in lines:
            a = accts.get(code) or Account(company_id=cid, code=code, name=f"Acct {code}", type="asset")
            if a.id is None:
                db.add(a)
                db.flush()
            db.add(JournalLine(entry_id=e.id, account_id=a.id, debit=dr, credit=cr))
        db.commit()
    finally:
        db.close()


def url(cid, fy=""):
    return f"/api/companies/{cid}/audit/trial-balance/generated{fy}"


def test_empty_books_say_so_and_do_not_claim_to_balance(owner):
    r = owner["client"].get(url(owner["cid"])).json()
    assert r["lines"] == [] and r["balanced"] is False
    assert any("nothing to show" in x for x in r["limitations"]) and any("opening balances" in x for x in r["limitations"])


def test_posted_entries_are_summed_by_account_within_the_financial_year(owner):
    cid, c = owner["cid"], owner["client"]
    post(cid, date(2025, 6, 1), [("9001", 1000, 0), ("9002", 0, 1000)])
    post(cid, date(2025, 7, 1), [("9001", 500, 0), ("9002", 0, 500)])
    post(cid, date(2024, 6, 1), [("9001", 7, 0), ("9002", 0, 7)])                      # earlier financial year: excluded
    post(cid, date(2025, 8, 1), [("9001", 99, 0), ("9002", 0, 99)], status="approved")  # not posted: excluded and reported
    r = c.get(url(cid, "?financial_year=2025-26")).json()
    by = {l["code"]: l for l in r["lines"]}
    assert by["9001"]["debit"] == "1500.00" and by["9002"]["credit"] == "1500.00"
    assert (r["total_debit"], r["total_credit"], r["difference"], r["balanced"]) == ("1500.00", "1500.00", "0.00", True)
    assert r["not_included"] == {"approved": 1} and any("not yet posted" in x for x in r["limitations"])
    assert (r["period_start"], r["period_end"]) == ("2025-04-01", "2026-03-31")


def test_an_unbalanced_book_shows_the_difference_and_is_not_adjusted(owner):
    cid, c = owner["cid"], owner["client"]
    post(cid, date(2025, 6, 1), [("9001", 1000, 0), ("9002", 0, 900)])
    r = c.get(url(cid, "?financial_year=2025-26")).json()
    assert r["balanced"] is False and r["difference"] == "100.00"


def test_another_clients_entries_never_appear(owner):
    c, cid = owner["client"], owner["cid"]
    other = c.post("/api/companies", json={"organization_id": owner["org_id"], "name": "Other Co"}).json()["id"]
    post(other, date(2025, 6, 1), [("9101", 5, 0), ("9102", 0, 5)])
    assert c.get(url(cid, "?financial_year=2025-26")).json()["lines"] == []
    assert c.get(url(other, "?financial_year=2025-26")).json()["total_debit"] == "5.00"
