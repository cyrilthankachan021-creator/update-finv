"""Playwright adapter for the GST portal (www.gst.gov.in), used by runner.py.

Security and safety:
- The browser window is visible and the person signs in themselves (username, password, captcha, OTP). FinV does not
  ask for, read, fill or store portal credentials, and each run uses a fresh browser profile (no saved cookies).
- Every click goes through safe_click, which refuses anything that submits, files, signs, pays or offsets liability.
  Confirmation dialogs are always dismissed. The final filing steps stay with the person.

The page addresses and selectors below follow the portal as documented in its user manuals but have not been
checked against a live session from this machine. If the portal layout differs, a step fails with PortalError,
the job stops as "portal_failed" and nothing is filed; adjust SELECTORS and resume.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .runner import PortalError

URLS = {
    "login": "https://services.gst.gov.in/services/login",
    "signed_in": r"https://(services|return)\.gst\.gov\.in/(services|returns)/auth/",
    "returns": "https://return.gst.gov.in/returns/auth/dashboard",
    "track": "https://return.gst.gov.in/returns/auth/trackreturnstatus",
}
SELECTORS = {
    "fy": "select[name='fin']",
    "quarter": "select[name='quarter']",
    "month": "select[name='mon']",
    "search": "button:has-text('Search')",
    "gstr1_tile": "[data-return='GSTR1'], .ret-tile:has-text('GSTR-1'), div.col-sm-4:has-text('GSTR1')",
    "gstr3b_tile": "[data-return='GSTR3B'], .ret-tile:has-text('GSTR-3B'), div.col-sm-4:has-text('GSTR3B')",
    "prepare_offline": "button:has-text('Prepare Offline')",
    "prepare_online": "button:has-text('Prepare Online')",
    "file_input": "input[type='file']",
    "upload_rows": "table:has(th:has-text('Reference')) tbody tr",
    "summary_button": "button:has-text('Generate Summary'), button:has-text('Generate GSTR-1 Summary')",
    "summary_area": "#summary, .summary, table:has-text('Total Taxable')",
    "3b_save": "button:has-text('Save GSTR3B'), button:has-text('Save')",
    "track_rows": "table:has(th:has-text('ARN')) tbody tr",
}
# Words on buttons/links FinV must never press. Filing, signing, paying and offsetting stay with the person.
FORBIDDEN = re.compile(r"submit|\bfile\b|file with|proceed to fil|\bevc\b|\bdsc\b|e-?sign|sign\b|"
                       r"make payment|\bpay\b|offset|create challan|declaration", re.I)
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
GSTR1_LABELS = {"taxable": "Total Taxable Value", "igst": "Integrated Tax", "cgst": "Central Tax",
                "sgst": "State/UT Tax", "cess": "Cess"}
GSTR3B_FIELDS = {   # 3B form field -> (payload path, input name on the portal form)
    "outward_taxable": (("sup_details", "osup_det", "txval"), "osup_det_txval"),
    "output_igst": (("sup_details", "osup_det", "iamt"), "osup_det_iamt"),
    "output_cgst": (("sup_details", "osup_det", "camt"), "osup_det_camt"),
    "output_sgst": (("sup_details", "osup_det", "samt"), "osup_det_samt"),
    "output_cess": (("sup_details", "osup_det", "csamt"), "osup_det_csamt"),
    "nil_exempt": (("sup_details", "osup_nil_exmp", "txval"), "osup_nil_exmp_txval"),
    "itc_igst": (("itc_elg", "itc_avl", 0, "iamt"), "itc_avl_oth_iamt"),
    "itc_cgst": (("itc_elg", "itc_avl", 0, "camt"), "itc_avl_oth_camt"),
    "itc_sgst": (("itc_elg", "itc_avl", 0, "samt"), "itc_avl_oth_samt"),
    "itc_cess": (("itc_elg", "itc_avl", 0, "csamt"), "itc_avl_oth_csamt"),
}


class SubmitBlocked(PortalError):
    pass


def _amount(text: str) -> Decimal | None:
    m = re.search(r"-?[\d,]+(?:\.\d+)?", text or "")
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None


class PlaywrightPortal:
    def __init__(self, headless: bool = False, login_timeout_s: int = 600, urls: dict | None = None,
                 selectors: dict | None = None):
        self.headless, self.login_timeout_s = headless, login_timeout_s
        self.urls, self.sel = {**URLS, **(urls or {})}, {**SELECTORS, **(selectors or {})}
        self._pw = self._browser = self.page = None

    # -------------------------------------------------------------- session
    def open(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        ctx = self._browser.new_context()          # fresh profile: no stored cookies or passwords
        self.page = ctx.new_page()
        self.page.on("dialog", lambda d: d.dismiss())   # never accept "are you sure you want to file?" prompts
        self.page.goto(self.urls["login"])

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def wait_for_login(self) -> None:
        """The person signs in in the visible window; FinV only waits for the signed-in page."""
        try:
            self.page.wait_for_url(re.compile(self.urls["signed_in"]), timeout=self.login_timeout_s * 1000)
        except Exception as exc:
            raise PortalError("Nobody signed in to the GST portal in time. Start the job again when ready.") from exc

    def taxpayer_gstin(self) -> str | None:
        found = set(GSTIN_RE.findall(self.page.inner_text("body")))
        return found.pop() if len(found) == 1 else (None if not found else "MULTIPLE")

    # -------------------------------------------------------------- safe interaction
    def safe_click(self, target) -> None:
        """target: a selector or a Playwright locator."""
        loc = (self.page.locator(target) if isinstance(target, str) else target).first
        if loc.count() == 0:
            raise PortalError("The portal page did not show an expected button; its layout may have changed.")
        label = " ".join(filter(None, [loc.inner_text(), loc.get_attribute("value"), loc.get_attribute("id"),
                                       loc.get_attribute("aria-label"), loc.get_attribute("name")]))
        if FORBIDDEN.search(label):
            raise SubmitBlocked(f"FinV refused to press '{label.strip()[:60]}': filing steps are left to you.")
        loc.click()

    def _select_containing(self, selector: str, text: str) -> None:
        sel = self.page.locator(selector).first
        if sel.count() == 0:
            raise PortalError("The return period selector was not found on the portal page.")
        for opt in sel.locator("option").all():
            if text.lower() in (opt.inner_text() or "").lower():
                sel.select_option(value=opt.get_attribute("value"))
                return
        raise PortalError(f"The portal did not offer the period {text}.")

    # -------------------------------------------------------------- returns
    def open_return(self, return_type: str, period: str) -> None:
        y, m = (int(x) for x in period.split("-"))
        fy = y if m >= 4 else y - 1
        self.page.goto(self.urls["returns"])
        self._select_containing(self.sel["fy"], f"{fy}-{str(fy + 1)[-2:]}")
        q = (m - 4) % 12 // 3 + 1
        self._select_containing(self.sel["quarter"], f"Quarter {q}")
        self._select_containing(self.sel["month"], MONTHS[m - 1])
        self.safe_click(self.sel["search"])
        tile = self.sel["gstr1_tile" if return_type == "GSTR1" else "gstr3b_tile"]
        self._tile = self.page.locator(tile).first
        self._tile.wait_for(timeout=30000)

    def gstr1_upload(self, path: str) -> str:
        self.safe_click(self._tile.locator(self.sel["prepare_offline"]))
        self.page.locator(self.sel["file_input"]).first.set_input_files(path)
        self.page.wait_for_timeout(2000)
        ref = self.latest_upload(since=None)
        if not ref:
            raise PortalError("The portal did not show a reference number for the upload.")
        return ref

    def _upload_rows(self) -> list[list[str]]:
        rows = []
        for tr in self.page.locator(self.sel["upload_rows"]).all():
            rows.append([c.strip() for c in tr.locator("td").all_inner_texts()])
        return rows

    def latest_upload(self, since: str | None) -> str | None:
        """Reference of the newest upload in the portal's history (rows: date, reference, status, ...)."""
        rows = self._upload_rows()
        if not rows:
            return None
        newest = rows[0]
        ref = next((c for c in newest if re.fullmatch(r"(?=.*\d)[A-Za-z0-9]{8,}", c)), None)
        if since and ref:
            when = next((c for c in newest if re.search(r"\d{2}[-/]\d{2}[-/]\d{4}", c)), None)
            if when:
                d = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", when)
                if date(int(d.group(3)), int(d.group(2)), int(d.group(1))) < date.fromisoformat(since[:10]):
                    return None
        return ref

    def upload_status(self, ref: str) -> str:
        self.page.reload()
        for row in self._upload_rows():
            if ref in row:
                text = " ".join(row).lower()
                if "processed with error" in text or "error" in text:
                    return "error"
                if "processed" in text:
                    return "processed"
                return "pending"
        return "pending"

    def gstr3b_fill(self, payload: dict) -> None:
        self.safe_click(self._tile.locator(self.sel["prepare_online"]))
        for _field, (path, name) in GSTR3B_FIELDS.items():
            v = payload
            for p in path:
                v = v[p]
            box = self.page.locator(f"input[name='{name}']").first
            if box.count() == 0:
                raise PortalError(f"The GSTR-3B form field for {_field} was not found.")
            box.fill(f"{v:.2f}")
        self.safe_click(self.sel["3b_save"])

    def summary_totals(self, return_type: str) -> dict:
        if return_type == "GSTR1":
            self.safe_click(self._tile.locator(self.sel["prepare_online"]))
            self.safe_click(self.sel["summary_button"])
            area = self.page.locator(self.sel["summary_area"]).first
            area.wait_for(timeout=60000)
            text = area.inner_text()
            out = {}
            for key, label in GSTR1_LABELS.items():
                m = re.search(re.escape(label) + r"[^\d-]*(-?[\d,]+(?:\.\d+)?)", text, re.I)
                out[key] = _amount(m.group(1)) if m else None
            return {k: (str(v) if v is not None else None) for k, v in out.items()}
        out = {}
        for key, (_, name) in GSTR3B_FIELDS.items():
            if key == "nil_exempt":
                continue
            box = self.page.locator(f"input[name='{name}']").first
            out[key] = str(_amount(box.input_value())) if box.count() else None
        return out

    def filing_status(self, return_type: str, period: str) -> dict:
        self.page.goto(self.urls["track"])
        y, m = period.split("-")
        rt = {"GSTR1": "GSTR1", "GSTR3B": "GSTR3B"}[return_type]
        for tr in self.page.locator(self.sel["track_rows"]).all():
            cells = [c.strip() for c in tr.locator("td").all_inner_texts()]
            text = " ".join(cells)
            if rt in text.replace("-", "") and (f"{m}{y}" in text or MONTHS[int(m) - 1] in text):
                arn = next((c for c in cells if re.fullmatch(r"[A-Z]{2}\d{2}[0-9A-Z]{8,16}", c)), None)
                status = next((c for c in cells if c.lower() in {"filed", "submitted", "not filed", "saved"}), None)
                when = next((c for c in cells if re.search(r"\d{2}[-/]\d{2}[-/]\d{4}", c)), None)
                return {"status": status, "arn": arn, "filed_at": when}
        return {"status": None, "arn": None, "filed_at": None}
