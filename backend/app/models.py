"""Database models.

Every financial record carries company_id; companies belong to an organization (CA firm).
Amounts use the Money type: NUMERIC on PostgreSQL, exact decimal strings on SQLite
(SQLite's NUMERIC affinity would otherwise round-trip through binary floats).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Money(TypeDecorator):
    impl = String(40)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(18, 2))
        return dialect.type_descriptor(String(40))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = Decimal(value)
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


# ---------------------------------------------------------------- identity & tenancy
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SessionToken(Base):
    __tablename__ = "session_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    # owner | admin | accountant | reviewer | auditor | viewer
    role: Mapped[str] = mapped_column(String(20))
    user: Mapped[User] = relationship()


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    # What the firm does for this client: "accounting", "audit" or both. Controls the workspaces offered.
    services: Mapped[list] = mapped_column(JSON, default=lambda: ["accounting", "audit"])
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CompanyAssignment(Base):
    """Limits who works on a client. A company with no assignments is open to its whole firm; once anyone is assigned, only
    them and the firm's owners and admins can open it."""
    __tablename__ = "company_assignments"
    __table_args__ = (UniqueConstraint("company_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class CompanySettings(Base):
    __tablename__ = "company_settings"
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    # local_only: no external AI calls at all. pseudonymized_ai: minimized, pseudonymized context may be sent.
    privacy_mode: Mapped[str] = mapped_column(String(30), default="local_only")
    retention_days: Mapped[int | None] = mapped_column(Integer, default=None)
    materiality_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("100000"))
    bank_match_window_days: Mapped[int] = mapped_column(Integer, default=3)
    vouching_threshold: Mapped[Decimal] = mapped_column(Money, default=Decimal("10000"))
    gst_filing: Mapped[str] = mapped_column(String(10), default="monthly")        # monthly | qrmp (quarterly return, monthly payment)
    tds_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    tan: Mapped[str | None] = mapped_column(String(10))


# ---------------------------------------------------------------- accounting master data
class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("company_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    # asset | liability | equity | income | expense
    type: Mapped[str] = mapped_column(String(20))
    # semantic role used by the engine, e.g. bank, payables, receivables, purchases, sales,
    # input_cgst, output_igst ... (nullable for ordinary accounts)
    role: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class Policy(Base):
    """Company-specific accounting policy. Only status == approved policies affect processing."""
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))          # stable policy identifier within the company
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(40))         # bank_keyword | party_account | tds_category
    definition: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | disabled | superseded
    source: Mapped[str] = mapped_column(String(30), default="manual")   # manual | review_correction | ai_suggestion
    rationale: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- documents & extracted records
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str | None] = mapped_column(String(500))
    # bank_statement | purchase_invoice | sales_invoice | credit_note | debit_note | invoice_register
    # | trial_balance | ledger
    kind: Mapped[str] = mapped_column(String(30))
    workspace: Mapped[str] = mapped_column(String(20), default="accounting")
    # uploaded | processing | processed | failed | repeated_upload | purged
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    error: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    extraction_notes: Mapped[list | None] = mapped_column(JSON)
    validation: Mapped[dict | None] = mapped_column(JSON)     # bank statements: accepted/rejected rows, period, balance checks
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceRecord(Base):
    """One extracted financial record: an invoice, a note, or a bank statement line."""
    __tablename__ = "source_records"
    __table_args__ = (
        Index("ix_records_company_type", "company_id", "record_type"),
        Index("ix_records_company_docno", "company_id", "doc_number_norm"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    row_index: Mapped[int | None] = mapped_column(Integer)
    # bank_txn | purchase_invoice | sales_invoice | credit_note | debit_note
    record_type: Mapped[str] = mapped_column(String(30))
    note_side: Mapped[str | None] = mapped_column(String(10))  # for notes: sales | purchase
    party_name: Mapped[str | None] = mapped_column(String(255))
    party_gstin: Mapped[str | None] = mapped_column(String(15))
    doc_number: Mapped[str | None] = mapped_column(String(100))
    doc_number_norm: Mapped[str | None] = mapped_column(String(100))
    doc_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    original_doc_number: Mapped[str | None] = mapped_column(String(100))  # notes -> invoice reference
    original_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id"))
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    taxable_amount: Mapped[Decimal | None] = mapped_column(Money)
    cgst: Mapped[Decimal | None] = mapped_column(Money)
    sgst: Mapped[Decimal | None] = mapped_column(Money)
    igst: Mapped[Decimal | None] = mapped_column(Money)
    cess: Mapped[Decimal | None] = mapped_column(Money)
    total_amount: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    category_hint: Mapped[str | None] = mapped_column(String(100))
    bank_account_code: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))
    debit_amount: Mapped[Decimal | None] = mapped_column(Money)   # bank: money out
    credit_amount: Mapped[Decimal | None] = mapped_column(Money)  # bank: money in
    balance: Mapped[Decimal | None] = mapped_column(Money)
    line_items: Mapped[list | None] = mapped_column(JSON)
    provenance: Mapped[dict | None] = mapped_column(JSON)   # field -> {source, confidence}
    raw: Mapped[dict | None] = mapped_column(JSON)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    # extracted | needs_review | proposed | validated | approved | posted | rejected | duplicate
    status: Mapped[str] = mapped_column(String(20), default="extracted")
    review_question: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    document: Mapped[Document | None] = relationship()


# ---------------------------------------------------------------- journal entries
class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id", ondelete="SET NULL"))
    entry_date: Mapped[date | None] = mapped_column(Date)
    narration: Mapped[str | None] = mapped_column(Text)
    treatment: Mapped[str | None] = mapped_column(String(60))
    # needs_review | validated | approved | posted | rejected
    status: Mapped[str] = mapped_column(String(20), default="needs_review")
    posting_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    explanation: Mapped[dict | None] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(20), default="engine")  # engine | manual | ai_assisted
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_edited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    lines: Mapped[list["JournalLine"]] = relationship(cascade="all, delete-orphan", order_by="JournalLine.id")
    source_record: Mapped[SourceRecord | None] = relationship()


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    description: Mapped[str | None] = mapped_column(String(255))
    account: Mapped[Account | None] = relationship()


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"), index=True)
    entry_version: Mapped[int | None] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(20))
    rule_version: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))    # pass | warning | error | needs_review | not_applicable
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (UniqueConstraint("record_id", "matched_record_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    matched_record_id: Mapped[int] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))       # duplicate | possible_alteration
    strength: Mapped[str] = mapped_column(String(20))   # exact | probable | possible
    score: Mapped[int] = mapped_column(Integer)
    signals: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")  # open | confirmed_duplicate | not_duplicate
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    record: Mapped[SourceRecord] = relationship(foreign_keys=[record_id])
    matched_record: Mapped[SourceRecord] = relationship(foreign_keys=[matched_record_id])


class Posting(Base):
    __tablename__ = "postings"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), unique=True)
    entry_version: Mapped[int] = mapped_column(Integer)
    posted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TallyExport(Base):
    """One row per posted entry sent to Tally (by file import confirmation or direct push)."""
    __tablename__ = "tally_exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), unique=True)
    batch_id: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(10))   # file | push
    status: Mapped[str] = mapped_column(String(20), default="exported")
    response: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BankMatch(Base):
    __tablename__ = "bank_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    bank_record_id: Mapped[int] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(20))   # auto | manual
    score: Mapped[int] = mapped_column(Integer, default=0)
    signals: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed | removed
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- audit workspace
class TrialBalanceImport(Base):
    __tablename__ = "tb_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20))   # trial_balance | ledger
    period_label: Mapped[str | None] = mapped_column(String(50))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrialBalanceLine(Base):
    __tablename__ = "tb_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("tb_imports.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    account_code: Mapped[str | None] = mapped_column(String(50))
    account_name: Mapped[str | None] = mapped_column(String(255))
    account_type: Mapped[str | None] = mapped_column(String(20))
    opening: Mapped[Decimal | None] = mapped_column(Money)   # debit-positive
    debit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    closing: Mapped[Decimal | None] = mapped_column(Money)   # debit-positive


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("tb_imports.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    entry_date: Mapped[date | None] = mapped_column(Date)
    account_code: Mapped[str | None] = mapped_column(String(50))
    account_name: Mapped[str | None] = mapped_column(String(255))
    voucher_no: Mapped[str | None] = mapped_column(String(100))
    voucher_type: Mapped[str | None] = mapped_column(String(50))
    party: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(100))
    narration: Mapped[str | None] = mapped_column(Text)
    debit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))


class AuditReportVersion(Base):
    """A frozen snapshot of the audit work at one moment. Never edited: a new version is created instead, and only a
    draft can be signed off."""
    __tablename__ = "audit_report_versions"
    __table_args__ = (UniqueConstraint("company_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")      # draft | signed_off | superseded
    snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    signed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sign_note: Mapped[str | None] = mapped_column(Text)


class VouchingLink(Base):
    __tablename__ = "vouching_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    ledger_entry_id: Mapped[int] = mapped_column(ForeignKey("ledger_entries.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"))
    result: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(20))   # consistent | discrepancy | needs_verification
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | agreed | queried
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditException(Base):
    __tablename__ = "audit_exceptions"
    __table_args__ = (UniqueConstraint("company_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("tb_imports.id", ondelete="CASCADE"))
    object_type: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[int | None] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))  # low | medium | high
    title: Mapped[str] = mapped_column(String(255))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    # open | needs_information | under_review | resolved | accepted_with_explanation
    status: Mapped[str] = mapped_column(String(30), default="open")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ExceptionComment(Base):
    __tablename__ = "exception_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("audit_exceptions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # None = automatic system action
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str | None] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- trail & AI metadata
class AuditEvent(Base):
    """Append-only, hash-chained activity log per company."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str | None] = mapped_column(String(40))
    object_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AIRequestLog(Base):
    """Metadata about external AI calls. Stores the minimized payload actually sent, never originals."""
    __tablename__ = "ai_request_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))    # ok | blocked | failed | invalid_response
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    sent_payload: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- assistant
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace: Mapped[str] = mapped_column(String(20))
    role: Mapped[str] = mapped_column(String(20))      # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSON)    # actions, navigation, tools used
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssistantAction(Base):
    """A change the assistant prepared. Nothing happens until the same user confirms it."""
    __tablename__ = "assistant_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | cancelled | failed | expired
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- GST returns (approval-gated portal preparation)
class GstReturn(Base):
    """One GST return (type + tax period) for a company, driven through a persistent state machine.

    States: prepared -> awaiting_approval -> approved -> portal_in_progress -> portal_ready_for_review
    -> filing_reported -> verified, plus portal_failed, invalidated and cancelled. Every change goes through
    gst_filing.transition(), a compare-and-set on lock_version, so concurrent approvals or jobs cannot both win.
    FinV never submits or files: the authorised person does that on the portal."""
    __tablename__ = "gst_returns"
    __table_args__ = (UniqueConstraint("company_id", "return_type", "period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    return_type: Mapped[str] = mapped_column(String(10))     # GSTR1 | GSTR3B
    period: Mapped[str] = mapped_column(String(7))           # YYYY-MM
    state: Mapped[str] = mapped_column(String(30), default="prepared")
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    current_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    approved_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    override: Mapped[dict | None] = mapped_column(JSON)      # documented authorised override of blocking warnings
    state_reason: Mapped[str | None] = mapped_column(Text)
    arn: Mapped[str | None] = mapped_column(String(40))
    portal_status: Mapped[str | None] = mapped_column(String(40))
    filed_at: Mapped[str | None] = mapped_column(String(40))   # as shown by the portal
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    reported_arn: Mapped[str | None] = mapped_column(String(40))  # typed by a user; unverified until the portal shows it
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GstReturnSnapshot(Base):
    """An immutable, versioned calculation of a return. Approval is bound to one snapshot."""
    __tablename__ = "gst_return_snapshots"
    __table_args__ = (UniqueConstraint("return_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("gst_returns.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    gstin: Mapped[str] = mapped_column(String(15))
    return_type: Mapped[str] = mapped_column(String(10))
    period: Mapped[str] = mapped_column(String(7))
    rules_version: Mapped[str] = mapped_column(String(20))
    totals: Mapped[dict] = mapped_column(JSON)
    breakdown: Mapped[dict] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON)
    reconciliation: Mapped[dict | None] = mapped_column(JSON)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    record_ids: Mapped[list] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)              # data prepared for the portal (GSTR-1 JSON / 3B values)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GstReviewLink(Base):
    """Single-use, expiring link sent by Telegram. It only opens the review screen after sign-in; it cannot approve."""
    __tablename__ = "gst_review_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("gst_returns.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("gst_return_snapshots.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    channel: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GstPortalJob(Base):
    """Portal preparation for one approved snapshot. Checkpoints make it resumable; the unique key prevents
    duplicate jobs. A lease (worker_id + heartbeat_at) stops two workers from running it at once."""
    __tablename__ = "gst_portal_jobs"
    __table_args__ = (UniqueConstraint("return_id", "snapshot_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("gst_returns.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("gst_return_snapshots.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))            # prepare | verify
    state: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | done | failed
    checkpoints: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    portal_totals: Mapped[dict | None] = mapped_column(JSON)
    differences: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
