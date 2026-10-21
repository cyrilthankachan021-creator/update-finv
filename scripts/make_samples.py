"""Generate FICTIONAL sample files for trying FinV. No real people, companies or GSTINs.

GSTINs are made up but carry a valid check digit so the format checks pass.
Run: backend/.venv/bin/python scripts/make_samples.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.engine.gst import gstin_check_digit  # noqa: E402


def g(state, pan):
    base = f"{state}{pan}1Z"
    return base + gstin_check_digit(base)


COMPANY = g("27", "AAACF1234K")
SHARMA = g("27", "BBBCS5678L")
KAVERI = g("29", "CCCCT9012M")
RAO = g("27", "DDDDC3456N")
out = Path(__file__).resolve().parent.parent / "samples"
out.mkdir(exist_ok=True)

(out / "README.txt").write_text(f"""Fictional sample data for FinV. Create a company with GSTIN {COMPANY} (state 27)
and an accounting period covering April 2025 to March 2026 (Settings > Company & periods).

1. purchase_register.csv  - purchases, a sales invoice and a credit note (Accounting > upload, type: detect)
2. purchase_register_resent.csv - the same INV-1001 re-keyed, plus an altered copy of INV-1001 -> duplicate + alteration
3. bank_statement.csv     - payment of INV-1001, a customer receipt, bank charges, and an unexplained credit
4. trial_balance.csv      - Audit > import as trial balance (contains a deliberate mis-keyed balance)
5. ledger.csv             - Audit > import as ledger (contains a duplicated rent entry)
""")

(out / "purchase_register.csv").write_text(f"""Type,Invoice No,Invoice Date,Party Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,IGST,Total,Category,Original Invoice No
Purchase,INV-1001,10/06/2025,Sharma Stationers,{SHARMA},27,10000.00,900.00,900.00,,11800.00,Office Expenses,
Purchase,KT/25/0457,12/06/2025,Kaveri Timber Traders,{KAVERI},27,50000.00,,,9000.00,59000.00,,
Sales,S-2025-031,14/06/2025,Rao Retail,{RAO},27,25000.00,2250.00,2250.00,,29500.00,,
Credit Note,CN-88,18/06/2025,Sharma Stationers,{SHARMA},27,1000.00,90.00,90.00,,1180.00,,INV-1001
Purchase,R-06,01/06/2025,Lakeview Estates,{SHARMA},27,40000.00,3600.00,3600.00,,47200.00,Rent,
""")

(out / "purchase_register_resent.csv").write_text(f"""Type,Invoice No,Invoice Date,Party Name,GSTIN,Place of Supply,Taxable Value,CGST,SGST,IGST,Total
Purchase,inv 1001,10/06/2025,Sharma Stationers Pvt Ltd,{SHARMA},27,10000.00,900.00,900.00,,11800.00
Purchase,INV-1001,10/06/2025,Sharma Stationers,{SHARMA},27,11000.00,990.00,990.00,,12980.00
""")

(out / "bank_statement.csv").write_text("""Statement of account (fictional)
Txn Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt,Closing Balance
01/06/2025,OPENING BALANCE,,,,"5,00,000.00"
20/06/2025,NEFT/SHARMA STATIONERS/INV-1001,UTR5501,"11,800.00",,"4,88,200.00"
24/06/2025,NEFT CR RAO RETAIL S-2025-031,UTR5502,,"29,500.00","5,17,700.00"
30/06/2025,SMS CHARGES QTR,,17.70,,"5,17,682.30"
30/06/2025,NEFT CR FROM ABC,UTR5503,,"2,00,000.00","7,17,682.30"
""")

(out / "trial_balance.csv").write_text("""Account Code,Account Name,Type,Opening Balance,Debit,Credit,Closing Balance
1100,Bank Account,Asset,500000.00,229500.00,11817.70,717682.30
1200,Trade Receivables,Asset,0,29500.00,29500.00,0
2100,Trade Payables,Liability,0,11800.00,118000.00,-106200.00
4100,Sales,Income,0,0,25000.00,-25000.00
5200,Rent,Expense,0,80000.00,0,80000.00
5600,Office Expenses,Expense,0,10000.00,0,10000.00
3100,Capital Account,Equity,-500000.00,0,200000.00,-690000.00
""")

(out / "ledger.csv").write_text("""Date,Account Code,Account Name,Voucher No,Party,Narration,Debit,Credit
01/06/2025,5200,Rent,JV-101,Lakeview Estates,June rent R-06,40000.00,0
01/06/2025,5200,Rent,JV-101,Lakeview Estates,June rent R-06,40000.00,0
10/06/2025,5600,Office Expenses,PV-210,Sharma Stationers,INV-1001 stationery,10000.00,0
15/06/2025,5600,Office Expenses,PV-211,Sharma Stationers,Courier,150000.00,0
""")
print("samples written to", out, "company GSTIN", COMPANY)
