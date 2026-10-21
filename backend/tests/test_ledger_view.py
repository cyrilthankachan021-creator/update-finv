"""Ledger view: totals, sorting, date filter and opening/closing balances from the client's own trial balance."""
from tests.conftest import upload

LEDGER = ("Date,Account Code,Account Name,Voucher No,Debit,Credit,Narration\n"
          "01/06/2025,1000,Cash,V1,500.00,0,first\n05/06/2025,1000,Cash,V2,0,200.00,second\n09/06/2025,2000,Rent,V3,700.00,0,third\n")
TB = "Account Code,Account Name,Opening,Debit,Credit,Closing\n1000,Cash,100.00,500.00,200.00,400.00\n"


def led(c, cid, qs=""):
    return c.get(f"/api/companies/{cid}/audit/ledger{qs}").json()


def test_totals_cover_every_matching_row_not_just_the_page(owner):
    c, cid = owner["client"], owner["cid"]
    assert upload(c, cid, "l.csv", LEDGER, kind="ledger", workspace="audit").status_code == 201
    r = led(c, cid, "?page_size=1")
    assert len(r["items"]) == 1 and r["total"] == 3
    assert r["totals"] == {"debit": "1200.00", "credit": "200.00"}


def test_sort_and_date_range(owner):
    c, cid = owner["client"], owner["cid"]
    upload(c, cid, "l.csv", LEDGER, kind="ledger", workspace="audit")
    assert [i["voucher_no"] for i in led(c, cid, "?sort=amount&dir=desc")["items"]] == ["V3", "V1", "V2"]
    assert [i["voucher_no"] for i in led(c, cid, "?from=2025-06-02&to=2025-06-06")["items"]] == ["V2"]
    assert c.get(f"/api/companies/{cid}/audit/ledger?sort=bogus").status_code == 422


def test_account_balances_come_from_the_trial_balance_or_say_they_are_missing(owner):
    c, cid = owner["client"], owner["cid"]
    upload(c, cid, "l.csv", LEDGER, kind="ledger", workspace="audit")
    none = led(c, cid, "?account=Cash")["balances"]
    assert none["opening"] is None and "No opening or closing" in none["note"]
    upload(c, cid, "tb.csv", TB, kind="trial_balance", workspace="audit")
    got = led(c, cid, "?account=Cash")["balances"]
    assert (got["opening"], got["closing"], got["note"]) == ("100.00", "400.00", None)
    assert led(c, cid)["balances"] is None                            # only shown for one account
