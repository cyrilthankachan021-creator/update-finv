import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { COMPANY, mockApi, renderApp, signedIn } from "./helpers";

const ROW = { ...COMPANY, overdue: 0, due_soon: 0, pending_tasks: 0, awaiting_review: 0, issues: 0, high_issues: 0, missing_data: true,
  financial_year: "2026-27", work_status: "missing_data", last_import_at: null };
const TDS = { applicable: false, tan: null, gst_filing: "monthly", payable: "0" };

describe("Client page", () => {
  it("shows Next steps with ticks, and says 'Not started' rather than 'Up to date' when nothing is imported", async () => {
    mockApi(signedIn([{ match: "/api/firm/clients/7", reply: { client: { ...ROW, gstin: "27AAACF1234K1ZU" }, tasks: [], issues: [{ key: "k", client_id: 7, client_name: "x", category: "missing_data", severity: "medium", title: "No data", description: "", detection: "", nature: "missing_data", review_status: "open", next_action: "", link: "/imports", created_at: null }], tds: TDS } }]));
    renderApp("/clients/7");
    const steps = await screen.findByRole("region", { name: "Next steps" });
    expect(within(steps).getByText(/Upload a bank statement or import from Tally/)).toBeInTheDocument();
    expect(within(steps).getAllByText("(to do)").length).toBeGreaterThanOrEqual(2);
    const bank = screen.getByRole("button", { name: /Bank Reconciliation/ });
    expect(within(bank).getByText("Not started")).toBeInTheDocument();
    expect(screen.queryByText("Up to date")).toBeInTheDocument();         // GST has its own task list, so it is not "not started"
  });

  it("ticks the first step once data has been imported", async () => {
    mockApi(signedIn([{ match: "/api/firm/clients/7", reply: { client: { ...ROW, missing_data: false, last_import_at: "2026-10-01T00:00:00" }, tasks: [], issues: [], tds: TDS } }]));
    renderApp("/clients/7");
    const steps = await screen.findByRole("region", { name: "Next steps" });
    expect(within(steps).getAllByText("(done)").length).toBeGreaterThanOrEqual(2);
  });
});

describe("Tab strips", () => {
  it("move with the arrow keys and stop at the first and last tab", async () => {
    mockApi(signedIn([{ match: /\/entries\?/, reply: { items: [], total: 0, page: 1, page_size: 25 } }]));
    renderApp("/accounting/entries");
    const tabs = await screen.findAllByRole("tab");
    const last = tabs.length - 1;
    await userEvent.click(tabs[0]);
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true");            // did not wrap to the end
    await userEvent.keyboard("{End}");
    expect(screen.getAllByRole("tab")[last]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getAllByRole("tab")[last]).toHaveAttribute("aria-selected", "true");         // did not wrap to the start
  });
});

const OVERVIEW = { imports: [], exceptions_by_status: {}, open_by_severity: {}, awaiting_review: 0, vouching: { linked: 0, discrepancies: 0, pending_review: 0 }, recent_activity: [] };

describe("Tally sync", () => {
  it("shows a failure plainly and never an invented success", async () => {
    mockApi(signedIn([{ match: /\/audit\/overview/, reply: OVERVIEW }, { match: /\/audit\/tally\/status$/, reply: { last_success: null, attempts: [] } },
      { method: "POST", match: /\/audit\/tally\/pull$/, status: 503, reply: { detail: "Tally did not answer at http://localhost:9000. Open TallyPrime with this company loaded." } }]));
    renderApp("/audit");
    await userEvent.click(await screen.findByRole("button", { name: "Fetch vouchers from Tally" }));
    expect(await screen.findByText(/Tally did not answer/)).toBeInTheDocument();
    expect(screen.queryByText(/voucher\(s\) read/)).not.toBeInTheDocument();
  });

  it("reports what came back, what was already there, and the months with nothing", async () => {
    mockApi(signedIn([{ match: /\/audit\/overview/, reply: OVERVIEW }, { match: /\/audit\/tally\/status$/, reply: { last_success: null, attempts: [] } },
      { method: "POST", match: /\/audit\/tally\/pull$/, reply: { ok: true, vouchers_read: 4, lines_read: 8, lines_added: 6, rejected: 0, months_without_vouchers: ["2025-07"], synced_at: "x", notes: [], status: "processed" } }]));
    renderApp("/audit");
    await userEvent.click(await screen.findByRole("button", { name: "Fetch vouchers from Tally" }));
    expect(await screen.findByText(/4 voucher\(s\) read, 6 new ledger line\(s\) added, 2 already imported earlier; 0 rejected/)).toBeInTheDocument();
    expect(screen.getByText(/No vouchers came back for: 2025-07/)).toBeInTheDocument();
  });
});

describe("Next steps are honest about GST", () => {
  it("does not tick 'Prepare GST returns' while a return is still not started", async () => {
    const task = { key: "g", client_id: 7, client_name: "x", gstin: "27AAACF1234K1ZU", category: "gst", task: "GSTR-1 return", period: "2026-09", due_date: "2026-11-11",
      status: "not_started", urgency: "upcoming", severity: "low", issue: "Not started", next_action: "Prepare the return.", link: "/accounting/gst", financial_year: "2026-27" };
    mockApi(signedIn([{ match: "/api/firm/clients/7", reply: { client: { ...ROW, gstin: "27AAACF1234K1ZU", missing_data: false, last_import_at: "2026-10-01T00:00:00" }, tasks: [task], issues: [], tds: TDS } }]));
    renderApp("/clients/7");
    const steps = await screen.findByRole("region", { name: "Next steps" });
    const gst = within(steps).getByText(/Prepare GST returns \(1 not started\)/).closest("li")!;
    expect(within(gst).getByText("(to do)")).toBeInTheDocument();
  });
});

describe("Error Center detail", () => {
  it("shows expected and actual values and the period, and sends the period filter", async () => {
    const calls = mockApi(signedIn([{ match: /^\/api\/firm\/issues/, reply: { items: [{ key: "a", client_id: 7, client_name: "Sample Traders Pvt Ltd", category: "audit_exception", severity: "high",
      title: "Closing balance does not agree", description: "Opening + debits - credits differs.", detection: "Audit rule TB-002", nature: "rule_detected", review_status: "open",
      next_action: "Investigate.", link: "/audit/exceptions/1", created_at: null, period: "FY 2025-26", financial_year: "2025-26", expected: "140.00", actual: "999.00" }], total: 1, page: 1, page_size: 25,
      facets: { categories: [], statuses: [] } } }, { match: /^\/api\/firm\/overview/, reply: { as_of: "2026-10-21", financial_year: "2026-27", attention: [], top_issues: [], not_tracked: [], summary: {} } }]));
    renderApp("/errors");
    expect(await screen.findByText(/Expected/)).toBeInTheDocument();
    expect(screen.getByText("140.00", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("FY 2025-26 · FY 2025-26")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Filter by period"), "2025");
    await vi.waitFor(() => expect(calls.some((c) => c.url.includes("period=2025"))).toBe(true));
  });
});
