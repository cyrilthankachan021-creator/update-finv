import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { COMPANY, ME, mockApi, renderApp, signedIn } from "./helpers";

const SNAP = {
  id: 3, version: 2, gstin: "27AAACF1234K1Z5", return_type: "GSTR1", period: "2025-06", rules_version: "gstr-2026.10",
  totals: { taxable: "161000.00", igst: "27360.00", cgst: "810.00", sgst: "810.00", cess: "0.00", documents: 4 },
  breakdown: { b2b: { taxable: "10000.00", igst: "0.00", cgst: "900.00", sgst: "900.00", cess: "0.00", count: 1 } },
  warnings: [{ code: "HSN_MISSING", severity: "blocking", message: "GSTR-1 Table 12 (HSN summary) is required." },
    { code: "NOT_COVERED", severity: "info", message: "Not prepared by FinV: exports." }],
  reconciliation: { status: "clear" }, payload_sha256: "a".repeat(64), documents: 4, tax_total: "28980.00", created_at: "2026-10-20T10:00:00",
};
const RET = {
  id: 5, return_type: "GSTR1", period: "2025-06", state: "awaiting_approval", reason: "Sent for approval.", snapshot: SNAP,
  approved_snapshot_id: null, approved_by: null, approved_at: null, override: null, arn: null, portal_status: null, filed_at: null,
  verified_at: null, reported_arn: null, prepare_job: null, verify_job: null, telegram_configured: true, can_open_window: true, history: [],
};

describe("GST returns", () => {
  it("shows the figures and binds the approval to the exact version reviewed", async () => {
    const calls = mockApi(signedIn([
      { match: /gst\/returns\/5$/, reply: RET },
      { method: "POST", match: /gst\/returns\/5\/approve$/, reply: { ...RET, state: "approved", approved_snapshot_id: 3, approved_by: "Asha Owner" } },
    ]));
    renderApp("/accounting/gst/5");
    expect(await screen.findByRole("heading", { name: "GSTR-1 · 2025-06" })).toBeInTheDocument();
    expect(screen.getByText("1,61,000.00")).toBeInTheDocument();
    expect(screen.getByText(/HSN summary/)).toBeInTheDocument();
    expect(screen.getByText(/FinV never submits, files, pays or signs/)).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();                       // needs the review tick, password and override reason
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.type(screen.getByLabelText(/Override reason/), "HSN summary entered on the portal");
    await userEvent.type(screen.getByLabelText("Your FinV password"), "secret-pass");
    await userEvent.click(approve);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(true));
    const body = calls.find((c) => c.url.endsWith("/approve"))!.body as Record<string, unknown>;
    expect(body).toEqual({ snapshot_id: 3, version: 2, payload_sha256: "a".repeat(64), password: "secret-pass",
      override_reason: "HSN summary entered on the portal" });
    expect(await screen.findByText(/Approved. You can now start portal preparation/)).toBeInTheDocument();
  });

  it("is listed in the Accounting navigation", async () => {
    mockApi(signedIn([{ match: /gst\/returns$/, reply: [] }]));
    renderApp("/accounting/gst");
    expect(await screen.findByRole("link", { name: "GST Returns" })).toBeInTheDocument();
    expect(await screen.findByText("No GST returns yet")).toBeInTheDocument();
  });

  it("opens a review link only after redeeming it, and shows why a used link fails", async () => {
    mockApi(signedIn([{ method: "POST", match: "/api/gst/review-links/redeem", status: 410, reply: { detail: "This review link has already been used. Open the return from FinV instead." } }]));
    renderApp("/gst/review?t=abcdefghijklmnop");
    expect(await screen.findByText(/already been used/)).toBeInTheDocument();
  });

  it("follows a valid review link to the return", async () => {
    mockApi(signedIn([
      { method: "POST", match: "/api/gst/review-links/redeem", reply: { company_id: 7, return_id: 5, snapshot_is_current: true } },
      { match: /gst\/returns\/5$/, reply: RET },
    ]));
    renderApp("/gst/review?t=abcdefghijklmnop");
    expect(await screen.findByRole("heading", { name: "GSTR-1 · 2025-06" })).toBeInTheDocument();
  });

  it("returns to the review link after signing in", async () => {
    const me: { match: string; reply: unknown; status: number } = { match: "/api/auth/me", reply: { detail: "Sign in" }, status: 401 };
    const calls = mockApi([
      me,
      { match: "/api/companies", reply: [COMPANY] },
      { method: "POST", match: "/api/auth/login", reply: () => { Object.assign(me, { reply: ME, status: 200 }); return ME; } },
      { method: "POST", match: "/api/gst/review-links/redeem", reply: { company_id: 7, return_id: 5, snapshot_is_current: true } },
      { match: /gst\/returns\/5$/, reply: RET },
    ]);
    renderApp("/gst/review?t=abcdefghijklmnop");
    await userEvent.type(await screen.findByLabelText(/Email/), "asha@example.test");
    await userEvent.type(screen.getByLabelText(/Password/), "pw");
    await userEvent.click(screen.getByRole("button", { name: /Sign in/ }));
    expect(await screen.findByRole("heading", { name: "GSTR-1 · 2025-06" })).toBeInTheDocument();
    expect((calls.find((c) => c.url.endsWith("/redeem"))!.body as { token: string }).token).toBe("abcdefghijklmnop");
  });
});
