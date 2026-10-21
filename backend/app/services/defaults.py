"""Starter chart of accounts for a new company (editable in Settings).

Account roles tell the engine which ledger plays which part (bank, payables, input CGST, ...).
These are generic ledger names, not client data.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..models import Account, AccountingPeriod, CompanySettings

STARTER_CHART = [
    ("1100", "Bank Account", "asset", "bank"),
    ("1150", "Cash in Hand", "asset", "cash"),
    ("1200", "Trade Receivables", "asset", "receivables"),
    ("1300", "Input CGST", "asset", "input_cgst"),
    ("1301", "Input SGST", "asset", "input_sgst"),
    ("1302", "Input IGST", "asset", "input_igst"),
    ("1303", "Input Cess", "asset", "input_cess"),
    ("1400", "TDS Receivable", "asset", "tds_receivable"),
    ("1500", "Furniture and Equipment", "asset", None),
    ("1600", "Inventory", "asset", None),
    ("2100", "Trade Payables", "liability", "payables"),
    ("2200", "Output CGST", "liability", "output_cgst"),
    ("2201", "Output SGST", "liability", "output_sgst"),
    ("2202", "Output IGST", "liability", "output_igst"),
    ("2203", "Output Cess", "liability", "output_cess"),
    ("2300", "TDS Payable", "liability", "tds_payable"),
    ("2400", "Loans Payable", "liability", None),
    ("3100", "Capital Account", "equity", None),
    ("4100", "Sales", "income", "sales"),
    ("4150", "Sales Returns", "income", "sales_returns"),
    ("4200", "Other Income", "income", None),
    ("4300", "Interest Income", "income", None),
    ("5100", "Purchases", "expense", "purchases"),
    ("5150", "Purchase Returns", "expense", "purchase_returns"),
    ("5200", "Rent", "expense", None),
    ("5300", "Salaries and Wages", "expense", None),
    ("5400", "Professional Fees", "expense", None),
    ("5500", "Bank Charges", "expense", None),
    ("5600", "Office Expenses", "expense", None),
    ("5700", "Electricity", "expense", None),
    ("5900", "Round Off", "expense", "round_off"),
]


def financial_year(d: date) -> tuple[date, date, str]:
    start_year = d.year if d.month >= 4 else d.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31), f"FY {start_year}-{str(start_year + 1)[-2:]}"


def default_privacy_mode() -> str:
    """New companies start with AI off unless the server owner set FINV_DEFAULT_PRIVACY_MODE in backend/.env.
    Admins can still change the mode per company in Settings."""
    from ..config import settings
    mode = settings.default_privacy_mode
    return mode if mode in {"local_only", "pseudonymized_ai", "cloud_ai"} else "local_only"


def seed_company(db: Session, company_id: int, today: date | None = None) -> None:
    for code, name, typ, role in STARTER_CHART:
        db.add(Account(company_id=company_id, code=code, name=name, type=typ, role=role))
    s, e, name = financial_year(today or date.today())
    db.add(AccountingPeriod(company_id=company_id, name=name, start_date=s, end_date=e))
    db.add(CompanySettings(company_id=company_id, privacy_mode=default_privacy_mode()))
