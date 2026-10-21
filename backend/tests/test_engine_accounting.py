from datetime import date
from decimal import Decimal as D

import pytest

from app.engine import accounting as eng
from app.engine.money import AmountError, fmt, parse_amount
from app.engine.normalize import norm_doc_number, norm_party
from app.engine.rules import Status
from tests.conftest import SUPPLIER_KA, SUPPLIER_MH

ROLES = [("1100", "Bank", "asset", "bank"), ("1200", "Receivables", "asset", "receivables"),
         ("1300", "Input CGST", "asset", "input_cgst"), ("1301", "Input SGST", "asset", "input_sgst"),
         ("1302", "Input IGST", "asset", "input_igst"), ("2100", "Payables", "liability", "payables"),
         ("2200", "Output CGST", "liability", "output_cgst"), ("2201", "Output SGST", "liability", "output_sgst"),
         ("2202", "Output IGST", "liability", "output_igst"), ("2300", "TDS Payable", "liability", "tds_payable"),
         ("4100", "Sales", "income", "sales"), ("4150", "Sales Returns", "income", "sales_returns"),
         ("5100", "Purchases", "expense", "purchases"), ("5150", "Purchase Returns", "expense", "purchase_returns"),
         ("5200", "Rent", "expense", None), ("5400", "Professional Fees", "expense", None),
         ("5900", "Round Off", "expense", "round_off"), ("9999", "Old ledger", "expense", None)]


@pytest.fixture()
def ctx():
    accts = [eng.AccountRef(i + 1, c, n, t, r, active=(c != "9999")) for i, (c, n, t, r) in enumerate(ROLES)]
    return eng.Context(chart=eng.Chart(accts), periods=[eng.Period("FY 2025-26", date(2025, 4, 1), date(2026, 3, 31))],
                       company_state="27")


def purchase(**kw):
    base = dict(id=1, record_type="purchase_invoice", party_name="Sharma Supplies", party_gstin=SUPPLIER_MH,
                doc_number="INV-001", doc_date=date(2025, 6, 10), place_of_supply="27", taxable=D("1000.00"),
                cgst=D("90.00"), sgst=D("90.00"), total=D("1180.00"), category_hint="Rent")
    base.update(kw)
    return eng.RecordData(**base)


def statuses(results):
    return {r.rule_id: r.status for r in results}


# ---------------------------------------------------------------- money
def test_parse_amount_formats():
    assert parse_amount("1,23,456.78") == D("123456.78")
    assert parse_amount("(500.00)") == D("-500.00")
    assert parse_amount("500 Cr") == D("-500")
    assert parse_amount("₹ 1,000") == D("1000")
    assert parse_amount(0.1) + parse_amount(0.2) == D("0.3")  # no binary float drift
    assert parse_amount("") is None
    with pytest.raises(AmountError):
        parse_amount("12abc")
    with pytest.raises(AmountError):
        parse_amount("-5", allow_negative=False)
    assert fmt(D("1234567.5")) == "12,34,567.50"


def test_doc_number_normalisation():
    assert norm_doc_number("INV-001") == norm_doc_number("inv 001") == norm_doc_number("INV001")
    assert norm_doc_number("INV-001") != norm_doc_number("INV-0010")
    assert norm_doc_number("2024/01") != norm_doc_number("202401")
    assert norm_party("M/s Sharma Supplies Pvt. Ltd.") == norm_party("SHARMA SUPPLIES PRIVATE LIMITED")


# ---------------------------------------------------------------- proposals & validation
def test_purchase_invoice_balanced_and_supported(ctx):
    p = eng.propose(purchase(), ctx)
    assert p.question is None  # category hint 'Rent' resolves the ledger
    res = eng.validate(p.lines, date(2025, 6, 10), purchase(), ctx, p.question)
    s = statuses(res)
    assert s["ACC-001"] == Status.PASS and s["ACC-007"] == Status.PASS and s["GST-004"] == Status.PASS
    assert eng.is_posting_eligible(res)
    assert sum(l.debit for l in p.lines) == sum(l.credit for l in p.lines) == D("1180.00")


def test_purchase_without_category_asks_question(ctx):
    rec = purchase(category_hint=None)
    p = eng.propose(rec, ctx)
    assert p.question and "Confirm the ledger" in p.question
    res = eng.validate(p.lines, rec.doc_date, rec, ctx, p.question)
    assert statuses(res)["ACC-010"] == Status.NEEDS_REVIEW
    assert not eng.is_posting_eligible(res)


def test_unbalanced_entry_is_error(ctx):
    lines = [eng.LineData(1, D("100.00"), D("0")), eng.LineData(6, D("0"), D("99.99"))]
    res = eng.validate(lines, date(2025, 6, 1), None, ctx)
    assert statuses(res)["ACC-001"] == Status.ERROR
    assert "difference 0.01" in next(r.message for r in res if r.rule_id == "ACC-001")


def test_balanced_but_wrong_evidence_is_not_verified(ctx):
    """Debits equal credits, but the entry books 1,000 to payables while the invoice says 1,180."""
    rec = purchase()
    lines = [eng.LineData(15, D("1000.00"), D("0")), eng.LineData(6, D("0"), D("1000.00"))]
    res = eng.validate(lines, rec.doc_date, rec, ctx)
    s = statuses(res)
    assert s["ACC-001"] == Status.PASS
    assert s["ACC-007"] == Status.ERROR
    assert not eng.is_posting_eligible(res)


def test_invalid_lines_inactive_account_and_precision(ctx):
    lines = [eng.LineData(18, D("10.005"), D("0")), eng.LineData(None, D("0"), D("10.005")),
             eng.LineData(1, D("5"), D("5"))]
    s = statuses(eng.validate(lines, date(2025, 6, 1), None, ctx))
    assert s["ACC-002"] == Status.ERROR and s["ACC-003"] == Status.ERROR and s["ACC-005"] == Status.ERROR


def test_period_rules(ctx):
    lines = [eng.LineData(1, D("1"), D("0")), eng.LineData(6, D("0"), D("1"))]
    assert statuses(eng.validate(lines, date(2024, 1, 1), None, ctx))["ACC-004"] == Status.NEEDS_REVIEW
    assert statuses(eng.validate(lines, None, None, ctx))["ACC-004"] == Status.ERROR
    ctx.periods[0].locked = True
    assert statuses(eng.validate(lines, date(2025, 6, 1), None, ctx))["ACC-004"] == Status.ERROR


def test_duplicate_posting_and_open_duplicates_block(ctx):
    rec = purchase()
    p = eng.propose(rec, ctx)
    ctx.other_active_entries = 1
    ctx.open_duplicates = [{"candidate_id": 1}]
    s = statuses(eng.validate(p.lines, rec.doc_date, rec, ctx))
    assert s["ACC-008"] == Status.ERROR and s["ACC-009"] == Status.NEEDS_REVIEW


def test_missing_total_no_entry(ctx):
    p = eng.propose(purchase(total=None), ctx)
    assert p.lines == [] and "total" in p.question.lower()


def test_derived_taxable_is_disclosed(ctx):
    p = eng.propose(purchase(taxable=None), ctx)
    assert any("derived" in u for u in p.explanation["uncertainties"])


def test_sales_invoice_inter_state(ctx):
    rec = eng.RecordData(id=2, record_type="sales_invoice", party_name="Rao Retail", party_gstin=SUPPLIER_KA,
                         doc_number="S-10", doc_date=date(2025, 7, 1), place_of_supply="29", taxable=D("2000"),
                         igst=D("360"), total=D("2360"))
    p = eng.propose(rec, ctx)
    res = eng.validate(p.lines, rec.doc_date, rec, ctx, p.question)
    assert eng.is_posting_eligible(res), [r.message for r in res if r.status != Status.PASS]
    codes = {ctx.chart.by_id(l.account_id).code: (l.debit, l.credit) for l in p.lines}
    assert codes["1200"] == (D("2360.00"), D("0")) and codes["2202"] == (D("0"), D("360.00"))


# ---------------------------------------------------------------- bank classification
def bank(**kw):
    base = dict(id=10, record_type="bank_txn", doc_date=date(2025, 6, 20), description="NEFT CR UNKNOWN",
                reference=None, credit=D("5000.00"))
    base.update(kw)
    return eng.RecordData(**base)


def test_bank_credit_not_inferred_from_direction(ctx):
    p = eng.propose(bank(), ctx)
    assert p.lines == [] and p.question
    assert "sales receipt, loan received, capital introduced" in p.explanation["summary"]
    # an unanswered question is pending review, not an error; an empty entry without a question is an error
    s = statuses(eng.validate(p.lines, date(2025, 6, 20), bank(), ctx, p.question))
    assert s["ACC-002"] == Status.NEEDS_REVIEW
    assert statuses(eng.validate([], date(2025, 6, 20), None, ctx))["ACC-002"] == Status.ERROR


def test_bank_payment_matched_by_invoice_reference(ctx):
    inv = purchase()
    ctx.open_items = [eng.OpenItem(inv, D("1180.00"))]
    rec = bank(credit=None, debit=D("1180.00"), description="NEFT/SHARMA SUPPLIES/INV-001")
    p = eng.propose(rec, ctx)
    assert p.treatment == "bank_supplier_payment" and p.question is None
    assert "invoice number INV-001" in p.explanation["summary"]
    assert eng.is_posting_eligible(eng.validate(p.lines, rec.doc_date, rec, ctx, p.question))


def test_bank_name_and_amount_only_needs_confirmation(ctx):
    ctx.open_items = [eng.OpenItem(purchase(), D("1180.00"))]
    rec = bank(credit=None, debit=D("1180.00"), description="PAYMENT SHARMA")
    p = eng.propose(rec, ctx)
    assert p.question and "Confirm" in p.question


def test_bank_policy_keyword(ctx):
    ctx.policies = [{"id": 1, "key": "kw:BANK FEE", "version": 2, "kind": "bank_keyword",
                     "definition": {"keyword": "SMS CHARGES", "account_code": "5400", "direction": "out"}}]
    rec = bank(credit=None, debit=D("17.70"), description="SMS CHARGES QTR")
    p = eng.propose(rec, ctx)
    assert p.treatment == "bank_policy" and "v2" in p.explanation["accounts"][1]["reason"]


def test_ai_suggestion_always_needs_review(ctx):
    ctx.ai_suggestion = {"account_code": "5200", "category": "Rent", "rationale": "rent", "confidence": 0.9}
    rec = bank(credit=None, debit=D("25000"), description="RENT JUNE")
    p = eng.propose(rec, ctx)
    assert p.origin == "ai_assisted" and p.question
    assert not eng.is_posting_eligible(eng.validate(p.lines, rec.doc_date, rec, ctx, p.question))


def test_bank_entry_amount_must_match_statement(ctx):
    rec = bank(credit=None, debit=D("100.00"))
    lines = [eng.LineData(15, D("90.00"), D("0")), eng.LineData(1, D("0"), D("90.00"))]
    assert statuses(eng.validate(lines, rec.doc_date, rec, ctx))["ACC-007"] == Status.ERROR


# ---------------------------------------------------------------- credit / debit notes
def test_credit_note_against_purchase(ctx):
    ctx.original = purchase()
    note = eng.RecordData(id=3, record_type="credit_note", party_name="Sharma Supplies", party_gstin=SUPPLIER_MH,
                          doc_number="CN-1", doc_date=date(2025, 6, 15), original_doc_number="INV-001",
                          place_of_supply="27", taxable=D("100"), cgst=D("9"), sgst=D("9"), total=D("118"))
    p = eng.propose(note, ctx)
    res = eng.validate(p.lines, note.doc_date, note, ctx, p.question)
    s = statuses(res)
    assert s["NOTE-002"] == Status.PASS and s["NOTE-003"] == Status.PASS and s["NOTE-004"] == Status.PASS
    codes = {ctx.chart.by_id(l.account_id).code: (l.debit, l.credit) for l in p.lines}
    assert codes["2100"] == (D("118.00"), D("0")) and codes["5150"] == (D("0"), D("100.00"))
    assert codes["1300"] == (D("0"), D("9.00"))
    assert eng.is_posting_eligible(res)


def test_credit_notes_cannot_exceed_invoice(ctx):
    ctx.original = purchase()
    ctx.prior_notes_total = D("1100.00")
    note = eng.RecordData(id=3, record_type="credit_note", party_gstin=SUPPLIER_MH, doc_number="CN-2",
                          doc_date=date(2025, 6, 15), original_doc_number="INV-001", taxable=D("100"), cgst=D("9"),
                          sgst=D("9"), total=D("118"), place_of_supply="27")
    s = statuses(eng.validate(eng.propose(note, ctx).lines, note.doc_date, note, ctx))
    assert s["NOTE-004"] == Status.ERROR


def test_note_without_original_or_side_asks(ctx):
    note = eng.RecordData(id=4, record_type="debit_note", party_name="X", doc_number="DN-1", doc_date=date(2025, 6, 1),
                          total=D("50"))
    p = eng.propose(note, ctx)
    assert p.lines == [] and "sale" in p.question
    s = statuses(eng.check_note(note, ctx))
    assert s["NOTE-001"] == Status.NEEDS_REVIEW


def test_note_party_mismatch_is_error(ctx):
    ctx.original = purchase()
    note = eng.RecordData(id=3, record_type="credit_note", party_gstin=SUPPLIER_KA, doc_number="CN-9",
                          original_doc_number="INV-001", total=D("10"))
    assert statuses(eng.check_note(note, ctx))["NOTE-003"] == Status.ERROR


def test_tds_only_from_approved_policy(ctx):
    rec = purchase(category_hint="Professional Fees", place_of_supply="27")
    p = eng.propose(rec, ctx)
    assert statuses(eng.validate(p.lines, rec.doc_date, rec, ctx, p.question))["TDS-001"] == Status.NOT_APPLICABLE
    ctx.policies = [{"id": 5, "key": "tds:5400", "version": 1, "kind": "tds_category",
                     "definition": {"account_code": "5400", "rate_percent": "10", "section_label": "Firm-verified section"}}]
    p = eng.propose(rec, ctx)
    tds = [l for l in p.lines if ctx.chart.by_id(l.account_id).code == "2300"]
    assert tds and tds[0].credit == D("100.00")
    res = eng.validate(p.lines, rec.doc_date, rec, ctx, p.question)
    s = statuses(res)
    assert s["ACC-001"] == Status.PASS and s["ACC-007"] == Status.PASS
    assert s["TDS-001"] == Status.NEEDS_REVIEW  # section and threshold must be confirmed by a person


def test_bank_reference_with_conflicting_same_number_records(ctx):
    ctx.open_items = [eng.OpenItem(purchase(id=1), D("1180.00")), eng.OpenItem(purchase(id=2, total=D("1298")), D("1298"))]
    rec = bank(credit=None, debit=D("1180.00"), description="NEFT/SHARMA/INV-001")
    p = eng.propose(rec, ctx)
    assert p.explanation["matched_record_id"] == 1 and p.question and "#2" in " ".join(p.explanation["uncertainties"])
