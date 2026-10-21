from datetime import date
from decimal import Decimal as D

from app.engine import audit_checks as ac
from app.engine import gst
from app.engine.duplicates import DupRecord, bank_running_balance, find_matches, line_item_check
from app.engine.reconciliation import BankLine, BookItem, reconcile
from app.engine.rules import Status
from tests.conftest import SUPPLIER_KA, SUPPLIER_MH, make_gstin


# ---------------------------------------------------------------- GST
def test_gstin_checksum():
    assert gst.validate_gstin(SUPPLIER_MH)[0]
    bad = SUPPLIER_MH[:-1] + ("A" if SUPPLIER_MH[-1] != "A" else "B")
    ok, msg = gst.validate_gstin(bad)
    assert not ok and "check digit" in msg
    assert not gst.validate_gstin("27ABC")[0]
    assert not gst.validate_gstin(make_gstin("27", "AAACF1234K")[:2].replace("27", "45") + make_gstin("27", "AAACF1234K")[2:])[0]


def doc(**kw):
    base = dict(record_type="purchase_invoice", side="purchase", party_gstin=SUPPLIER_MH, place_of_supply="27",
                taxable=D("1000"), cgst=D("90"), sgst=D("90"), igst=None, cess=None, total=D("1180"), company_state="27")
    base.update(kw)
    return gst.TaxDoc(**base)


def st(results):
    return {r.rule_id: r.status for r in results}


def test_gst_arithmetic_and_roundoff():
    assert st(gst.check_tax_document(doc()))["GST-002"] == Status.PASS
    assert st(gst.check_tax_document(doc(total=D("1180.40"))))["GST-002"] == Status.WARNING
    assert st(gst.check_tax_document(doc(total=D("1280"))))["GST-002"] == Status.ERROR


def test_gst_structure():
    assert st(gst.check_tax_document(doc(igst=D("180"), total=D("1360"))))["GST-003"] == Status.ERROR
    assert st(gst.check_tax_document(doc(sgst=D("80"), total=D("1170"))))["GST-003"] == Status.ERROR


def test_place_of_supply_missing_is_review_not_pass():
    r = st(gst.check_tax_document(doc(place_of_supply=None)))
    assert r["GST-004"] == Status.NEEDS_REVIEW


def test_place_of_supply_mismatch():
    # Karnataka supplier, POS Maharashtra, but CGST+SGST charged -> review (not auto error)
    r = st(gst.check_tax_document(doc(party_gstin=SUPPLIER_KA)))
    assert r["GST-004"] == Status.NEEDS_REVIEW
    r = st(gst.check_tax_document(doc(party_gstin=SUPPLIER_KA, cgst=None, sgst=None, igst=D("180"))))
    assert r["GST-004"] == Status.PASS


def test_rate_plausibility_and_itc():
    assert st(gst.check_tax_document(doc(cgst=D("70"), sgst=D("70"), total=D("1140"))))["GST-005"] == Status.WARNING
    assert st(gst.check_tax_document(doc(party_gstin=None)))["GST-006"] == Status.NEEDS_REVIEW


# ---------------------------------------------------------------- duplicates & integrity
def inv(id, **kw):
    base = dict(id=id, record_type="purchase_invoice", party_name="Sharma Supplies", party_gstin=SUPPLIER_MH,
                doc_number="INV-001", doc_date=date(2025, 6, 10), taxable=D("1000"), cgst=D("90"), sgst=D("90"),
                total=D("1180"), document_id=id)
    base.update(kw)
    return DupRecord(**base)


def test_exact_duplicate_with_normalised_number():
    m = find_matches(inv(2, doc_number="inv 001"), [inv(1)])
    assert m[0].strength == "exact" and m[0].kind == "duplicate"
    assert "same after normalising" in m[0].explanation


def test_duplicate_from_different_file_same_invoice():
    m = find_matches(inv(2, file_sha256="b" * 64), [inv(1, file_sha256="a" * 64)])
    assert m and m[0].strength == "exact"


def test_changed_amount_is_possible_alteration_not_fraud():
    m = find_matches(inv(2, total=D("1280"), taxable=D("1100")), [inv(1)])
    assert m[0].kind == "possible_alteration"
    assert "Document inconsistency" in m[0].explanation and "fraud" not in m[0].explanation.lower()
    fields = {d["field"] for d in m[0].signals["differences"]}
    assert fields == {"taxable", "total"}


def test_supplier_identity_difference_not_matched_as_same_party():
    assert find_matches(inv(2, party_gstin=SUPPLIER_KA), [inv(1)]) == []


def test_legitimate_recurring_same_amount_is_only_possible():
    m = find_matches(inv(2, doc_number="INV-777"), [inv(1)])
    assert m[0].strength == "possible" and "legitimate" in m[0].explanation
    assert find_matches(inv(2, doc_number="INV-777", doc_date=date(2025, 7, 10)), [inv(1)]) == []


def test_duplicate_credit_and_debit_notes():
    cn = dict(record_type="credit_note", doc_number="CN-5", total=D("118"), taxable=D("100"), cgst=D("9"), sgst=D("9"))
    assert find_matches(inv(2, **cn), [inv(1, **cn)])[0].strength == "exact"
    dn = {**cn, "record_type": "debit_note", "doc_number": "DN-5"}
    assert find_matches(inv(2, **dn), [inv(1, **dn)])[0].strength == "exact"
    # a credit note never duplicates an invoice with the same number
    assert find_matches(inv(2, **{**cn, "doc_number": "INV-001"}), [inv(1)]) == []


def test_bank_duplicates():
    a = DupRecord(1, "bank_txn", doc_date=date(2025, 6, 1), debit=D("500"), reference="UTR123", description="X", document_id=1)
    b = DupRecord(2, "bank_txn", doc_date=date(2025, 6, 1), debit=D("500"), reference="UTR123", description="X", document_id=2)
    c = DupRecord(3, "bank_txn", doc_date=date(2025, 6, 1), debit=D("500"), reference="UTR999", description="X", document_id=2)
    assert find_matches(b, [a])[0].strength == "exact"
    assert find_matches(c, [a]) == []  # similar amount, different reference -> separate


def test_running_balance_and_line_items():
    rows = [(1, None, D("1000"), D("1000")), (2, D("200"), None, D("800")), (3, D("100"), None, D("750"))]
    breaks = bank_running_balance(rows)
    assert len(breaks) == 1 and breaks[0]["row"] == 3
    assert not line_item_check(inv(1, line_items=[{"amount": "600"}, {"amount": "300"}]))["ok"]


# ---------------------------------------------------------------- reconciliation
def test_reconciliation_matching_and_unmatched():
    bank = [BankLine(1, date(2025, 6, 2), D("1180"), "out", "NEFT INV-001", "INV-001"),
            BankLine(2, date(2025, 6, 3), D("999"), "in", "UNKNOWN"),
            BankLine(3, date(2025, 6, 5), D("500"), "out", "CHQ 11", "11")]
    book = [BookItem(10, date(2025, 6, 1), D("1180"), "out", "Paid INV-001"),
            BookItem(11, date(2025, 6, 20), D("500"), "out", "Rent"),     # outside window
            BookItem(12, date(2025, 6, 5), D("505"), "out", "Other")]
    r = reconcile(bank, book, 3)
    assert r.matched[0]["bank_record_id"] == 1 and r.matched[0]["entry_id"] == 10
    assert set(r.unmatched_bank) == {2, 3} and set(r.unmatched_book) == {11, 12}
    assert any(n["bank_record_id"] == 3 and n["entry_id"] == 12 for n in r.near_misses)
    assert reconcile(bank, book, 3).matched == r.matched  # reproducible


def test_reconciliation_ambiguous_same_amount():
    bank = [BankLine(1, date(2025, 6, 2), D("100"), "out", "FEE")]
    book = [BookItem(10, date(2025, 6, 2), D("100"), "out", "A"), BookItem(11, date(2025, 6, 2), D("100"), "out", "B")]
    r = reconcile(bank, book)
    assert r.matched == [] and r.ambiguous[0]["candidate_entry_ids"] == [10, 11]


# ---------------------------------------------------------------- audit checks
def test_trial_balance_checks():
    lines = [ac.TBLine(1, 1, "1100", "Bank Account", "asset", D("1000"), D("500"), D("200"), D("1300")),
             ac.TBLine(2, 2, "2100", "Trade Creditors", "liability", D("-1000"), D("0"), D("300"), D("-1300")),
             ac.TBLine(3, 3, "4100", "Sales", "income", D("0"), D("0"), D("100"), D("-50")),     # does not roll
             ac.TBLine(4, 4, "1100", "Cash", "asset", D("0"), D("0"), D("0"), D("-10"))]       # dup code, abnormal
    summary, f = ac.check_trial_balance(lines, D("400"))
    rules = {x.rule_id for x in f}
    assert {"TB-001", "TB-002", "TB-003", "TB-005", "TB-007"} <= rules
    assert summary["difference"] != "0"


def test_ledger_and_vouching():
    l1 = ac.LedgerLine(1, 1, date(2025, 6, 1), "5200", "Rent", "JV1", "Landlord", None, "June rent", D("100000"), D("0"))
    l2 = ac.LedgerLine(2, 2, date(2025, 6, 1), "5200", "Rent", "JV1", "Landlord", None, "June rent", D("100000"), D("0"))
    l3 = ac.LedgerLine(3, 3, date(2025, 6, 8), "5600", "Office", "JV2", None, None, "Sunday", D("50"), D("0"))
    f = ac.check_ledger([l1, l2, l3], D("50000"), (date(2025, 4, 1), date(2025, 5, 31)))
    rules = {x.rule_id for x in f}
    assert {"LED-001", "LED-002", "LED-003", "LED-005"} <= rules
    ev = ac.Evidence(9, "purchase_invoice", "Landlord", None, "JV1", date(2025, 6, 2), D("100000"))
    assert ac.vouch(l1, ev)["outcome"] == "consistent"
    ev_bad = ac.Evidence(9, "purchase_invoice", "Someone Else", None, "INV-9", date(2025, 8, 1), D("90000"))
    res = ac.vouch(l1, ev_bad)
    assert res["outcome"] == "discrepancy" and {"amount", "date", "counterparty", "reference"} <= {
        c["check"] for c in res["checks"] if c["ok"] is False}
    missing = ac.missing_evidence([l1, l3], {1}, D("1000"))
    assert [m.object_id for m in missing] == []  # l1 linked, l3 below threshold


def test_vouching_net_of_gst_and_bank_evidence():
    exp = ac.LedgerLine(1, 1, date(2025, 6, 10), "5600", "Office", "PV-210", "Sharma Stationers", None,
                        "INV-1001 stationery", D("10000"), D("0"))
    inv = ac.Evidence(2, "purchase_invoice", "Sharma Stationers", None, "INV-1001", date(2025, 6, 10), D("11800"),
                      taxable=D("10000"))
    res = ac.vouch(exp, inv)
    assert res["outcome"] == "consistent" and "taxable value" in res["checks"][0]["detail"]
    pay = ac.LedgerLine(3, 2, date(2025, 6, 20), "2100", "Payables", "BP-1", None, None, "Paid INV-1001", D("11800"), D("0"))
    bank = ac.Evidence(4, "bank_txn", None, None, None, date(2025, 6, 20), D("11800"), "UTR5501",
                       description="NEFT/SHARMA STATIONERS/INV-1001")
    res = ac.vouch(pay, bank)
    ref = next(c for c in res["checks"] if c["check"] == "reference")
    assert res["outcome"] == "consistent" and ref["ok"] is True and "INV1001" in ref["detail"]
    # a bank line can never vouch a net-of-GST amount
    assert ac.vouch(exp, bank)["outcome"] == "discrepancy"


def test_vouching_does_not_call_an_entry_verified_when_almost_nothing_was_compared():
    # Only the amount can be compared: no dates, no party, no reference. That is not verification.
    entry = ac.LedgerLine(1, 1, None, "5600", "Office", None, None, None, None, D("500"), D("0"))
    ev = ac.Evidence(2, "purchase_invoice", None, None, None, None, D("500"))
    res = ac.vouch(entry, ev)
    assert res["outcome"] == "needs_verification" and "manually" in res["summary"]
    # with a second field agreeing it becomes consistent, and says which fields agreed
    entry2 = ac.LedgerLine(1, 1, date(2025, 6, 10), "5600", "Office", None, None, None, None, D("500"), D("0"))
    ev2 = ac.Evidence(2, "purchase_invoice", None, None, None, date(2025, 6, 10), D("500"))
    res2 = ac.vouch(entry2, ev2)
    assert res2["outcome"] == "consistent" and "amount, date" in res2["summary"]
