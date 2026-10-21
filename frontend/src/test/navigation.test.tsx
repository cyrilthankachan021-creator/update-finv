import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StartNotice } from "../components/StartNotice";
import { StepNav } from "../components/Stepper";
import { arrowNav, AT_START_EVENT, clampIndex, installStartGuard, restoreScroll, useStepper } from "../lib/navigation";
import { COMPANY, ME, mockApi, renderApp } from "./helpers";

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

describe("Back stops at the first page", () => {
  it("never leaves the app: Back from the first page lands on the marker, steps forward again and says so", async () => {
    const off = installStartGuard(window);
    const seen = vi.fn();
    window.addEventListener(AT_START_EVENT, seen);
    try {
      expect(window.history.length).toBeGreaterThanOrEqual(2);         // marker under the first real entry
      window.history.pushState({ idx: 1 }, "", "/a");                  // the user went somewhere
      window.history.back(); await tick();                              // back to the first page: allowed
      expect(seen).not.toHaveBeenCalled();
      window.history.back(); await tick();                              // back again: would leave FinV
      expect(seen).toHaveBeenCalledTimes(1);
      expect(window.history.state).toBeNull();                          // we are on the first real page, not the marker
      window.history.back(); await tick();                              // pressing Back again does the same, with no loop
      expect(seen).toHaveBeenCalledTimes(2);
      expect(window.history.state).toBeNull();
    } finally { off(); window.removeEventListener(AT_START_EVENT, seen); }
  });

  it("announces 'You're at the start' briefly", async () => {
    render(<StartNotice />);
    expect(screen.queryByText("You're at the start")).not.toBeInTheDocument();
    act(() => { window.dispatchEvent(new Event(AT_START_EVENT)); });
    expect(await screen.findByText("You're at the start")).toBeInTheDocument();
  });
});

describe("First and last steps", () => {
  function Flow({ onFinish }: { onFinish: () => void }) {
    const s = useStepper(3);
    return <div><p>Current: {s.index + 1}</p><StepNav stepper={s} onFinish={onFinish} /></div>;
  }

  it("Back is disabled on the first step and Next becomes Finish on the last; nothing wraps", async () => {
    const done = vi.fn();
    render(<Flow onFinish={done} />);
    const back = screen.getByRole("button", { name: "Back" });
    expect(back).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Current: 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Finish" }));
    expect(done).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Current: 3")).toBeInTheDocument();           // Finish did not wrap to step 1
    await userEvent.click(back); await userEvent.click(back);
    expect(screen.getByText("Current: 1")).toBeInTheDocument();
    expect(back).toBeDisabled();
  });

  it("arrow keys stop at the first and last item", () => {
    expect(clampIndex(-1, 4)).toBe(0);
    expect(clampIndex(9, 4)).toBe(3);
    const set = vi.fn();
    const key = (k: string, i: number) => { set.mockClear(); arrowNav(i, 4, set)({ key: k, preventDefault: () => undefined }); return set.mock.calls[0]?.[0]; };
    expect(key("ArrowLeft", 0)).toBe(0);
    expect(key("ArrowRight", 3)).toBe(3);
    expect(key("ArrowRight", 1)).toBe(2);
    expect(key("End", 0)).toBe(3);
    expect(key("a", 1)).toBeUndefined();
    set.mockClear(); arrowNav(0, 4, set, true)({ key: "ArrowUp", preventDefault: () => undefined });
    expect(set).toHaveBeenCalledWith(0);
  });
});

describe("History entries", () => {
  it("changing an entries tab replaces the entry instead of piling up history", async () => {
    mockApi([{ match: "/api/auth/me", reply: ME }, { match: "/api/companies", reply: [COMPANY] }, { match: /\/entries\?/, reply: { items: [], total: 0, page: 1, page_size: 25 } }]);
    const { router } = renderApp(["/", "/accounting/entries"]);
    await screen.findByRole("tab", { name: "Approved" });
    await userEvent.click(screen.getByRole("tab", { name: "Approved" }));
    await waitFor(() => expect(router.state.location.search).toContain("status=approved"));
    expect(router.state.historyAction).toBe("REPLACE");
    await act(async () => { await router.navigate(-1); });
    expect(router.state.location.pathname).toBe("/");                    // one Back goes to the previous page, not the previous tab
  });
});

describe("Unsaved work", () => {
  let calls: ReturnType<typeof mockApi> = [];
  const setup = () => {
    calls = mockApi([{ match: "/api/auth/me", reply: ME }, { match: "/api/companies", reply: [COMPANY] },
      { match: /^\/api\/firm\/clients\?/, reply: { items: [], total: 0, page: 1, page_size: 25 } }]);
    return renderApp(["/", "/clients"], { company: false });
  };

  it("asks 'Leave without saving?' when Back is pressed in a half-filled form, and Stay keeps the input", async () => {
    const { router } = setup();
    await userEvent.click((await screen.findAllByRole("button", { name: "Add new client" }))[0]);
    await userEvent.type(screen.getByLabelText(/Company name/), "Half Done Pvt Ltd");
    await act(async () => { void router.navigate(-1); });
    const ask = await screen.findByRole("dialog", { name: "Leave without saving?" });
    await userEvent.click(within(ask).getByRole("button", { name: "Stay" }));
    expect(router.state.location.pathname).toBe("/clients");
    expect(screen.getByLabelText(/Company name/)).toHaveValue("Half Done Pvt Ltd");     // nothing was lost
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);                    // the prompt sits inside the form: Stay must not submit it
    await act(async () => { void router.navigate(-1); });
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Leave without saving?" })).getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("asks before switching client with a half-filled form, and stays on the same client if you stay", async () => {
    const { router } = setup();
    await userEvent.click((await screen.findAllByRole("button", { name: "Add new client" }))[0]);
    await userEvent.type(screen.getByLabelText(/Company name/), "Half Done Pvt Ltd");
    await userEvent.selectOptions(screen.getByLabelText("Client", { selector: "select" }), String(COMPANY.id));
    const ask = await screen.findByRole("dialog", { name: "Leave without saving?" });
    expect(router.state.location.pathname).toBe("/clients");                       // not moved yet
    await userEvent.click(within(ask).getByRole("button", { name: "Stay" }));
    expect(router.state.location.pathname).toBe("/clients");
    expect(screen.getByLabelText(/Company name/)).toHaveValue("Half Done Pvt Ltd");
  });

  it("does not ask when the form is empty", async () => {
    const { router } = setup();
    await userEvent.click((await screen.findAllByRole("button", { name: "Add new client" }))[0]);
    await act(async () => { await router.navigate(-1); });
    expect(screen.queryByRole("dialog", { name: "Leave without saving?" })).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("Sensitive flows", () => {
  it("Back after opening a GST review link goes to the page before it and never redeems the link again", async () => {
    const calls = mockApi([{ match: "/api/auth/me", reply: ME }, { match: "/api/companies", reply: [COMPANY] },
      { method: "POST", match: "/api/gst/review-links/redeem", reply: { company_id: COMPANY.id, return_id: 5 } },
      { match: /\/gst\/returns\/5$/, reply: { detail: "not needed here" }, status: 404 },
      { match: /^\/api\/firm\/overview/, reply: { as_of: "2026-10-21", financial_year: "2026-27", attention: [], top_issues: [], not_tracked: [],
        summary: { clients: 0, gst_pending: 0, gst_overdue: 0, awaiting_review: 0, due_soon: 0, issues_open: 0, issues_high: 0, audit_findings: 0, missing_data: 0 } } }]);
    const { router } = renderApp(["/", "/gst/review?t=abc"]);
    await waitFor(() => expect(router.state.location.pathname).toBe("/accounting/gst/5"));
    expect(router.state.historyAction).toBe("REPLACE");                   // the link's own entry was replaced, not kept
    await act(async () => { await router.navigate(-1); });
    expect(router.state.location.pathname).toBe("/");
    await tick(50);
    expect(calls.filter((c) => c.url.includes("review-links/redeem"))).toHaveLength(1);
  });
});

describe("Scroll position", () => {
  const fake = (height: number) => {
    const w = { document: { documentElement: { scrollHeight: height } }, innerHeight: 800, scrollTo: vi.fn(),
      requestAnimationFrame: (cb: () => void) => setTimeout(cb, 0) as unknown as number, cancelAnimationFrame: (id: number) => clearTimeout(id) };
    return w as unknown as Window & { scrollTo: ReturnType<typeof vi.fn> };
  };

  it("returns to the saved position once the list is tall enough", async () => {
    const w = fake(300);
    restoreScroll(1200, w);
    expect(w.scrollTo).not.toHaveBeenCalled();                            // page not loaded yet
    (w.document.documentElement as { scrollHeight: number }).scrollHeight = 5000;
    await tick();
    expect(w.scrollTo).toHaveBeenCalledWith(0, 1200);
  });

  it("gives up after a while instead of waiting forever", async () => {
    const w = fake(300);
    restoreScroll(1200, w, 3);
    await tick(50);
    expect(w.scrollTo).toHaveBeenCalledTimes(1);
  });
});
