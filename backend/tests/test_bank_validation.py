"""Bank statement validation report: period, accepted and rejected rows, and the statement's own balance checks."""
from decimal import Decimal

from app.engine.bank_validation import bank_report
from tests.conftest import upload

GOOD = ("Date,Description,Reference,Debit,Credit,Balance\n"
        "01/06/2025,Opening sale,R1,,1000.00,11000.00\n"
        "05/06/2025,Rent,R2,2500.00,,8500.00\n"
        "30/06/2025,Receipt,R3,,500.00,9000.00\n")


def report(c, cid, name, text):
    r = upload(c, cid, name, text, kind="bank_statement")
    assert r.status_code in (200, 201), r.text
    return r.json()["validation"]


def test_clean_statement_passes_with_period_and_totals(owner):
    v = report(owner["client"], owner["cid"], "good.csv", GOOD)
    assert v["status"] == "passed"
    assert (v["period_start"], v["period_end"]) == ("2025-06-01", "2025-06-30")
    assert (v["lines_accepted"], v["lines_rejected"]) == (3, 0)
    assert (v["total_debits"], v["total_credits"]) == ("2500.00", "1500.00")
    assert all(ch["ok"] for ch in v["checks"])


def test_rejected_rows_are_counted_and_listed_with_the_reason(owner):
    bad = GOOD + "not-a-date,Mystery,R4,100.00,,8900.00\n"
    v = report(owner["client"], owner["cid"], "bad.csv", bad)
    assert v["status"] == "needs_attention"
    assert v["lines_accepted"] == 3 and v["lines_rejected"] >= 1
    assert v["rejected"][0]["row"] == 4 and v["rejected"][0]["message"]


def test_a_broken_running_balance_is_reported_with_the_row(owner):
    broken = GOOD.replace("8500.00", "8400.00")
    v = report(owner["client"], owner["cid"], "broken.csv", broken)
    assert v["status"] == "needs_attention"
    assert v["balance_breaks"] and v["balance_breaks"][0]["row"] in (2, 3)
    assert any(ch["check"] == "Running balance agrees" and ch["ok"] is False for ch in v["checks"])


def test_without_a_balance_column_the_check_says_it_could_not_run():
    recs = [{"row_index": 1, "doc_date": __import__("datetime").date(2025, 6, 1), "debit_amount": Decimal("5"), "credit_amount": None}]
    v = bank_report(recs, [])
    assert [c for c in v["checks"] if c["check"] == "Running balance agrees"][0]["ok"] is None
    assert v["status"] == "passed"


def test_repeated_lines_are_flagged():
    d = __import__("datetime").date(2025, 6, 1)
    line = {"doc_date": d, "debit_amount": Decimal("5"), "credit_amount": None, "reference": "X", "description": "same"}
    v = bank_report([{**line, "row_index": 1}, {**line, "row_index": 2}], [])
    assert v["repeated_lines"] == 1 and v["status"] == "needs_attention"
