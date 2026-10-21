import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderApp, signedIn } from "./helpers";

const V = (o: object) => ({ id: 1, version: 1, status: "draft", note: null, created_by: "Asha Owner", created_at: "2026-10-21T10:00:00", signed_by: null,
  signed_at: null, sign_note: null, open_exceptions: 0, open_high: 0, content_hash: "abc", ...o });

describe("Audit report versions", () => {
  it("signs off a clean draft with one click", async () => {
    const list = [V({})];
    const calls = mockApi(signedIn([{ method: "POST", match: /report-versions\/1\/sign-off$/, reply: () => { list[0] = V({ status: "signed_off", signed_by: "Asha Owner", signed_at: "2026-10-21T11:00:00" }); return list[0]; } },
      { match: /report-versions$/, reply: () => list }]));
    renderApp("/audit/reports");
    await userEvent.click(await screen.findByRole("button", { name: "Sign off v1" }));
    expect(await screen.findByText(/Signed by Asha Owner/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/sign-off"))?.body).toEqual({ acknowledge_open: false });
  });

  it("will not sign with open exceptions until the person ticks the acknowledgement", async () => {
    mockApi(signedIn([{ match: /report-versions$/, reply: [V({ open_exceptions: 2, open_high: 1 })] }]));
    renderApp("/audit/reports");
    const btn = await screen.findByRole("button", { name: "Sign off v1" });
    expect(btn).toBeDisabled();
    await userEvent.click(within(btn.closest("td")!).getByRole("checkbox"));
    expect(btn).toBeEnabled();
  });

  it("shows a superseded version without a sign-off button", async () => {
    mockApi(signedIn([{ match: /report-versions$/, reply: [V({ id: 2, version: 2 }), V({ status: "superseded" })] }]));
    renderApp("/audit/reports");
    expect(await screen.findAllByRole("button", { name: /Sign off/ })).toHaveLength(1);
  });
});
