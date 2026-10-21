import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../App";
import { SessionProvider } from "../session";

export type Handler = (url: string, init: RequestInit) => unknown | Promise<unknown>;
export interface Route { method?: string; match: RegExp | string; reply: unknown | Handler; status?: number }

export const ME = { id: 1, name: "Asha Owner", email: "asha@example.test", organizations: [{ id: 1, name: "Fictional & Co", role: "owner" }] };
export const COMPANY = { id: 7, name: "Sample Traders Pvt Ltd", gstin: null, state_code: "27", currency: "INR", organization_id: 1, role: "owner" };

/** Install a fetch mock that answers from a route table; records every call. */
export function mockApi(routes: Route[]) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method || "GET").toUpperCase();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body ?? null;
    calls.push({ method, url, body });
    const r = routes.find((x) => (x.method ?? "GET") === method && (typeof x.match === "string" ? url === x.match : x.match.test(url)));
    if (!r) return new Response(JSON.stringify({ detail: `No mock for ${method} ${url}` }), { status: 404 });
    const payload = typeof r.reply === "function" ? await (r.reply as Handler)(url, init) : r.reply;
    if (payload && typeof payload === "object" && "__ndjson" in payload) {
      const lines = (payload as { __ndjson: unknown[] }).__ndjson.map((e) => JSON.stringify(e) + "\n").join("");
      return new Response(lines, { status: r.status ?? 200, headers: { "Content-Type": "application/x-ndjson" } });
    }
    return new Response(JSON.stringify(payload), { status: r.status ?? 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function signedIn(extra: Route[] = [], withCompany = true): Route[] {
  return [
    ...extra,
    { match: "/api/auth/me", reply: ME },
    { match: "/api/companies", reply: withCompany ? [COMPANY] : [] },
  ];
}

export function renderApp(path: string | string[], { company = true, index }: { company?: boolean; index?: number } = {}) {
  if (company) localStorage.setItem("finv.company", String(COMPANY.id));
  const entries = Array.isArray(path) ? path : [path];
  const router = createMemoryRouter([{ path: "*", element: <SessionProvider><App /></SessionProvider> }],
    { initialEntries: entries, initialIndex: index ?? entries.length - 1, future: { v7_relativeSplatPath: true, v7_fetcherPersist: true, v7_normalizeFormMethod: true, v7_partialHydration: true, v7_skipActionErrorRevalidation: true } });
  const view = render(<RouterProvider router={router} future={{ v7_startTransition: true }} />);
  return { ...view, router };
}
