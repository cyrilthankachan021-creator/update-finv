"""Browser walkthrough of the Definition-of-Done workflow against a running FinV (fictional data).

Usage: python scripts/e2e_browser.py [base_url] [screenshot_dir]
Requires: pip install playwright && playwright install chromium
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5180"
SHOTS = Path(sys.argv[2] if len(sys.argv) > 2 else "e2e-shots")
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SHOTS.mkdir(parents=True, exist_ok=True)
EMAIL = f"demo{int(time.time())}@example.test"
errors: list[str] = []


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def side(page, name):
    page.locator("nav.sidebar").get_by_role("link", name=name, exact=True).click()


def upload(page, kind_label, file):
    page.get_by_label("Document type").select_option(label=kind_label)
    page.get_by_test_id("file-input").set_input_files(str(SAMPLES / file))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    # a 401 from the initial "am I signed in?" probe on the register page is expected
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}")
            if m.type == "error" and "401" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    # 1-2. open FinV, create account
    page.goto(BASE + "/register")
    page.get_by_label("Your name").fill("Demo Accountant")
    page.get_by_label("Firm or organisation name").fill("Fictional & Associates")
    page.get_by_label("Email").fill(EMAIL)
    page.get_by_label("Password").fill("demo-password-123")
    page.get_by_role("button", name="Create account").click()

    # 4. create the test company (onboarding before the workspaces)
    expect(page.get_by_role("heading", name="Add a company")).to_be_visible()
    shot(page, "01-onboarding")
    page.get_by_label("Company name").fill("Sample Traders Pvt Ltd")
    page.get_by_label("GSTIN Optional").fill("27AAACF1234K1ZU")
    page.get_by_role("button", name="Create company").click()

    # landing: two choices
    expect(page.get_by_role("heading", name="Welcome to FinV, Demo")).to_be_visible()
    shot(page, "02-landing")

    # add the FY 2025-26 period the samples use (Settings is a secondary location)
    page.get_by_role("button", name="Demo").click()
    page.get_by_role("menuitem", name="Settings").click()
    page.get_by_label("Period name").fill("FY 2025-26")
    page.get_by_label("Start date").fill("2025-04-01")
    page.get_by_label("End date").fill("2026-03-31")
    page.get_by_role("button", name="Add period").click()
    expect(page.get_by_text("Period added.")).to_be_visible()
    page.get_by_role("link", name="FinV home").click()

    # 3. select Accounting
    page.get_by_role("button", name="Accounting:").click()
    expect(page.get_by_text("Upload financial documents")).to_be_visible()
    shot(page, "03-accounting-empty")

    # 5-8. upload, extract, propose, validate
    upload(page, "Detect from columns (CSV/XLSX)", "purchase_register.csv")
    expect(page.get_by_text("5 records extracted and checked.")).to_be_visible(timeout=15000)
    upload(page, "Bank statement", "bank_statement.csv")
    expect(page.get_by_text("4 records extracted and checked.")).to_be_visible(timeout=15000)
    # 9. known duplicate scenario
    upload(page, "Detect from columns (CSV/XLSX)", "purchase_register_resent.csv")
    expect(page.get_by_text("2 records extracted and checked.")).to_be_visible(timeout=15000)
    page.reload()
    expect(page.get_by_role("button", name="Possible duplicates")).to_be_visible()
    shot(page, "04-accounting-overview")

    side(page, "Transactions")
    expect(page.get_by_role("table")).to_be_visible()
    shot(page, "05-transactions")

    # 10. explanation of the duplicate
    side(page, "Duplicates")
    expect(page.get_by_text("Possible alteration").first).to_be_visible()
    shot(page, "06-duplicates")
    # confirm the exact duplicate and the altered copy
    for _ in range(2):
        page.get_by_role("button", name="Confirm duplicate").first.click()
        page.get_by_role("dialog").get_by_role("textbox").fill("Same supplier bill entered twice")
        page.get_by_role("dialog").get_by_role("button", name="Confirm").click()
        page.wait_for_timeout(600)

    # 11-12. correct the Kaveri purchase (needs a ledger), revalidate
    side(page, "Transactions")
    page.get_by_role("cell", name="KT/25/0457").click()
    expect(page.get_by_text("Confirm the ledger for this purchase").first).to_be_visible()
    shot(page, "07-record-needs-review")
    page.get_by_role("button", name="Correct entry").click()
    page.get_by_label("Ledger for line 1").select_option(label="1500 · Furniture and Equipment")
    page.get_by_label("Note (optional)").fill("Timber for office furniture: capital item")
    page.get_by_role("button", name="Save and re-check").click()
    expect(page.get_by_text("Validated", exact=True).first).to_be_visible(timeout=10000)
    shot(page, "08-record-corrected")

    # 13. approve and post INV-1001
    side(page, "Transactions")
    page.get_by_role("row").filter(has_text="INV-1001").filter(has_text="Validated").first.click()
    page.get_by_role("button", name="Approve").click()
    page.get_by_role("dialog").get_by_role("button", name="Approve").click()
    page.get_by_role("button", name="Post to ledger").click()
    expect(page.get_by_role("heading", name="Posted entry")).to_be_visible()
    shot(page, "09-posted")

    # bank: unknown credit asks a question
    side(page, "Transactions")
    page.get_by_role("cell", name="NEFT CR FROM ABC").click()
    expect(page.get_by_text("FinV cannot determine whether this bank credit").first).to_be_visible()
    shot(page, "10-bank-unclassified")

    side(page, "Bank Reconciliation")
    expect(page.get_by_role("heading", name="Bank reconciliation")).to_be_visible()
    shot(page, "11-bank-recon")

    # 14. audit trail
    side(page, "Reports")
    expect(page.get_by_text("Chain verified")).to_be_visible()
    shot(page, "12-reports-audit-trail")

    # Audit workspace
    page.get_by_role("link", name="FinV home").click()
    page.get_by_role("button", name="Audit:").click()
    page.get_by_label("Financial period").fill("FY 2025-26")
    page.get_by_label("Period start").fill("2025-04-01")
    page.get_by_label("Period end").fill("2026-03-31")
    upload(page, "Trial balance", "trial_balance.csv")
    expect(page.get_by_text("Processed").first).to_be_visible(timeout=15000)
    upload(page, "Ledger / day book", "ledger.csv")
    page.wait_for_timeout(1200)
    page.reload()
    shot(page, "13-audit-overview")
    side(page, "Trial Balance")
    expect(page.get_by_text("Total debits").first).to_be_visible()
    shot(page, "14-trial-balance")
    side(page, "Ledger Review")
    page.get_by_role("cell", name="INV-1001 stationery").click()
    page.get_by_label("Search documents").fill("INV-1001")
    page.get_by_role("button", name="Search documents").click()
    page.get_by_role("row").filter(has_text="Purchase invoice").get_by_role("button", name="Link").first.click()
    expect(page.get_by_text("Evidence agrees").or_(page.get_by_text("Evidence differs")).first).to_be_visible()
    shot(page, "15-vouching")
    side(page, "Exceptions")
    page.get_by_role("row").filter(has_text="Possible duplicate entries").first.click()
    page.get_by_label("Change status").select_option(label="Needs information")
    page.get_by_label("Comment").fill("Asked the client for both rent vouchers")
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Asked the client for both rent vouchers")).to_be_visible()
    shot(page, "16-exception")

    # responsive: phone width
    cid = page.evaluate("localStorage.getItem('finv.company')")
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.context.add_cookies(page.context.cookies())
    mobile.goto(BASE + "/login")
    mobile.evaluate(f"localStorage.setItem('finv.company', '{cid}')")
    mobile.goto(BASE + "/")
    mobile.wait_for_timeout(800)
    shot(mobile, "17-mobile-landing")
    mobile.goto(BASE + "/accounting")
    mobile.wait_for_timeout(800)
    shot(mobile, "18-mobile-accounting")
    overflow = mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    if overflow:
        errors.append("mobile: page scrolls horizontally on /accounting")
    browser.close()

print("screenshots:", SHOTS.resolve())
print("errors:" if errors else "no browser errors")
for e in errors:
    print(" -", e)
sys.exit(1 if errors else 0)
