"""Pulling the Day Book from a (fake) running Tally."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.test_tally_import import VOUCHERS

REQUESTS: list[str] = []


class FakeTally(BaseHTTPRequestHandler):
    reply = VOUCHERS.encode()
    code = 200

    def do_POST(self):
        REQUESTS.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(self.reply)

    def log_message(self, *a):
        pass


@pytest.fixture()
def tally():
    FakeTally.reply, FakeTally.code = VOUCHERS.encode(), 200
    REQUESTS.clear()
    srv = HTTPServer(("127.0.0.1", 0), FakeTally)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def pull(c, cid, url, **kw):
    return c.post(f"/api/companies/{cid}/audit/tally/pull", json={"url": url, "date_from": "2025-06-01", "date_to": "2025-08-31", **kw})


def test_a_pull_imports_the_vouchers_and_reports_what_it_did(owner, tally):
    c, cid = owner["client"], owner["cid"]
    r = pull(c, cid, tally)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["vouchers_read"] == 1 and out["lines_added"] == 3 and out["rejected"] == 0
    assert out["months_without_vouchers"] == ["2025-07", "2025-08"]                 # it says which months came back empty
    assert "<ID>Day Book</ID>" in REQUESTS[0] and "<SVFROMDATE>20250601</SVFROMDATE>" in REQUESTS[0]
    st = c.get(f"/api/companies/{cid}/audit/tally/status").json()
    assert st["last_success"] and st["attempts"][0]["ok"] is True


def test_syncing_twice_does_not_duplicate_anything(owner, tally):
    c, cid = owner["client"], owner["cid"]
    pull(c, cid, tally)
    again = pull(c, cid, tally).json()
    assert again["status"] == "repeated_upload" or again["lines_added"] == 0
    assert c.get(f"/api/companies/{cid}/audit/ledger?page_size=100").json()["total"] == 3


def test_failures_are_reported_as_failures(owner, tally):
    c, cid = owner["client"], owner["cid"]
    FakeTally.reply = b"<ENVELOPE><LINEERROR>Company 'X' is not loaded</LINEERROR></ENVELOPE>"
    r = pull(c, cid, tally)
    assert r.status_code == 422 and "not loaded" in r.json()["detail"] and "Nothing was imported" in r.json()["detail"]
    assert c.get(f"/api/companies/{cid}/audit/ledger").json()["total"] == 0
    FakeTally.code = 500
    assert pull(c, cid, tally).status_code == 502
    assert pull(c, cid, "http://127.0.0.1:1").status_code == 503                           # nothing listening: honest, with the prerequisite
    st = c.get(f"/api/companies/{cid}/audit/tally/status").json()
    assert st["last_success"] is None and all(a["ok"] is False for a in st["attempts"])


def test_only_local_addresses_and_valid_periods_are_accepted(owner, tally):
    c, cid = owner["client"], owner["cid"]
    assert pull(c, cid, "http://example.com:9000").status_code == 422
    assert pull(c, cid, tally, date_from="2025-09-01", date_to="2025-06-01").status_code == 422
    assert pull(c, cid, tally, date_from="2023-01-01", date_to="2025-06-01").status_code == 422
