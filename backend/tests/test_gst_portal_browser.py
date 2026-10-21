"""Drives the real Playwright adapter against a local mock of the GST portal (headless Chromium).

The mock has Submit / File with EVC buttons that record any press: the test proves FinV never presses them, that
the uploaded JSON reproduces the approved totals, and that safe_click refuses filing controls outright.
It does not prove the selectors match the live portal; that needs a real session.
"""
from __future__ import annotations

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.portal.gst_portal import PlaywrightPortal, SubmitBlocked
from tests.conftest import COMPANY_GSTIN
from tests.test_gst_filing import approved_return, run

pytest.importorskip("playwright.sync_api")


class MockPortal:
    def __init__(self):
        self.uploads: list[dict] = []
        self.pressed: list[str] = []
        self.filed = False

    def totals(self) -> dict:
        t = {k: Decimal("0") for k in ("txval", "iamt", "camt", "samt", "csamt")}
        if not self.uploads:
            return t
        p = self.uploads[-1]["payload"]
        items = [(1, i) for s in p.get("b2b", []) for inv in s["inv"] for i in inv["itms"]]
        items += [(1, i) for s in p.get("b2cl", []) for inv in s["inv"] for i in inv["itms"]]
        items += [(-1 if nt["ntty"] == "C" else 1, i) for s in p.get("cdnr", []) for nt in s["nt"] for i in nt["itms"]]
        items += [(-1 if nt["ntty"] == "C" else 1, i) for nt in p.get("cdnur", []) for i in nt["itms"]]
        for sign, i in items:
            for k in t:
                t[k] += sign * Decimal(str(i["itm_det"][k]))
        for e in p.get("b2cs", []):
            for k in t:
                t[k] += Decimal(str(e[k]))
        return t

    def page(self, path: str) -> str:
        if path == "/login":
            # stands in for the person signing in (password, captcha, OTP) in the visible window
            return "<p>Login</p><script>setTimeout(()=>location.href='/services/auth/fowelcome', 200)</script>"
        if path == "/services/auth/fowelcome":
            return f"<p>Welcome</p><p>GSTIN: {COMPANY_GSTIN}</p>"
        if path == "/returns/auth/dashboard":
            rows = "".join(f"<tr><td>20-10-2026</td><td>{u['ref']}</td><td>Processed</td></tr>"
                           for u in reversed(self.uploads))
            t = self.totals()
            return f"""<p>GSTIN - {COMPANY_GSTIN}</p>
<select name="fin"><option value="2024">2024-25</option><option value="2025">2025-26</option></select>
<select name="quarter"><option value="q1">Quarter 1 (Apr - Jun)</option><option value="q2">Quarter 2 (Jul - Sep)</option></select>
<select name="mon"><option value="04">April</option><option value="06">June</option></select>
<button>Search</button>
<div data-return="GSTR1">GSTR-1 <button>Prepare Offline</button> <button>Prepare Online</button></div>
<input type="file" onchange="this.files[0].text().then(t=>fetch('/upload',{{method:'POST',body:t}})).then(()=>location.reload())">
<table><thead><tr><th>Date</th><th>Reference ID</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>
<button>Generate GSTR-1 Summary</button>
<div id="summary">Total Taxable Value {t['txval']:.2f} Integrated Tax {t['iamt']:.2f} Central Tax {t['camt']:.2f}
 State/UT Tax {t['samt']:.2f} Cess {t['csamt']:.2f}</div>
<button id="submit" onclick="fetch('/press/submit',{{method:'POST'}})">SUBMIT</button>
<button onclick="if(confirm('File?'))fetch('/press/evc',{{method:'POST'}})">FILE WITH EVC</button>"""
        if path == "/returns/auth/trackreturnstatus":
            row = ("<tr><td>GSTR1</td><td>062025</td><td>AA2706250012345</td><td>11-07-2025</td><td>Filed</td></tr>"
                   if self.filed else "")
            return f"<table><thead><tr><th>Return</th><th>Period</th><th>ARN</th></tr></thead><tbody>{row}</tbody></table>"
        return "not found"


@pytest.fixture()
def mock_portal():
    state = MockPortal()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = state.page(self.path).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/upload":
                state.uploads.append({"ref": f"RF{len(state.uploads) + 1:08d}", "payload": json.loads(data)})
            elif self.path.startswith("/press/"):
                state.pressed.append(self.path)
            self.send_response(204)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    state.urls = {"login": f"{base}/login", "signed_in": r"http://127\.0\.0\.1:\d+/services/auth/",
                  "returns": f"{base}/returns/auth/dashboard", "track": f"{base}/returns/auth/trackreturnstatus"}
    yield state
    srv.shutdown()


def adapter(mock):
    return PlaywrightPortal(headless=True, login_timeout_s=20, urls=mock.urls)


def test_browser_job_uploads_matches_and_never_files(owner, mock_portal):
    c, cid = owner["client"], owner["cid"]
    ret = approved_return(owner)
    job = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/portal-job").json()["prepare_job"]
    assert run(job["id"], adapter(mock_portal)) == "done", \
        c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()["reason"]
    assert len(mock_portal.uploads) == 1 and mock_portal.uploads[0]["payload"]["fp"] == "062025"
    assert mock_portal.pressed == []
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "portal_ready_for_review" and d["prepare_job"]["differences"] == []

    # the person files on the portal; FinV verifies from the portal's own status page
    mock_portal.filed = True
    v = c.post(f"/api/companies/{cid}/gst/returns/{ret['id']}/filed", json={}).json()["verify_job"]
    assert run(v["id"], adapter(mock_portal)) == "done"
    d = c.get(f"/api/companies/{cid}/gst/returns/{ret['id']}").json()
    assert d["state"] == "verified" and d["arn"] == "AA2706250012345" and d["portal_status"] == "Filed"
    assert mock_portal.pressed == []


def test_safe_click_refuses_filing_controls(mock_portal):
    p = adapter(mock_portal)
    p.open()
    try:
        p.wait_for_login()
        p.page.goto(mock_portal.urls["returns"])
        for target in ("#submit", "button:has-text('FILE WITH EVC')"):
            with pytest.raises(SubmitBlocked):
                p.safe_click(target)
        p.page.wait_for_timeout(300)
        assert mock_portal.pressed == []
        p.safe_click("button:has-text('Search')")      # ordinary controls still work
    finally:
        p.close()
