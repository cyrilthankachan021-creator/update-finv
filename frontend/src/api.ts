// Thin API client. Same-origin cookies (HttpOnly session) + an anti-CSRF header on every call.

export class ApiError extends Error {
  status: number;
  details: Record<string, unknown> | null;
  constructor(status: number, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

function messageFrom(body: unknown, status: number): { message: string; details: Record<string, unknown> | null } {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return { message: d, details: body as Record<string, unknown> };
    if (d && typeof d === "object" && "message" in d) return { message: String((d as { message: unknown }).message), details: d as Record<string, unknown> };
  }
  if (status === 0) return { message: "FinV could not reach its local service. Check that it is running.", details: null };
  return { message: "Something went wrong. Nothing was changed. Try again.", details: null };
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "finv", ...(opts.headers || {}) };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    const m = messageFrom(null, 0);
    throw new ApiError(0, m.message);
  }
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = null; }
  if (!res.ok) {
    const m = messageFrom(parsed, res.status);
    throw new ApiError(res.status, m.message, m.details);
  }
  return parsed as T;
}

/** Upload with progress and cancellation (fetch has no upload progress). */
export function uploadFile(path: string, form: FormData, onProgress: (pct: number) => void, idempotencyKey: string): { promise: Promise<unknown>; cancel: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<unknown>((resolve, reject) => {
    xhr.open("POST", path);
    xhr.setRequestHeader("X-Requested-With", "finv");
    xhr.setRequestHeader("Idempotency-Key", idempotencyKey);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      let parsed: unknown = null;
      try { parsed = JSON.parse(xhr.responseText); } catch { parsed = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(parsed);
      else { const m = messageFrom(parsed, xhr.status); reject(new ApiError(xhr.status, m.message, m.details)); }
    };
    xhr.onerror = () => reject(new ApiError(0, messageFrom(null, 0).message));
    xhr.onabort = () => reject(new ApiError(-1, "Upload cancelled."));
    xhr.send(form);
  });
  return { promise, cancel: () => xhr.abort() };
}

export const money = (v: string | null | undefined): string => {
  if (v === null || v === undefined || v === "") return "—";
  const neg = v.startsWith("-");
  const [whole, frac = "00"] = (neg ? v.slice(1) : v).split(".");
  let head = whole.slice(0, -3), out = whole.slice(-3);
  while (head.length > 2) { out = head.slice(-2) + "," + out; head = head.slice(0, -2); }
  if (head) out = head + "," + out;
  return `${neg ? "-" : ""}${out}.${frac.padEnd(2, "0").slice(0, 2)}`;
};

export const dateFmt = (v: string | null | undefined): string => {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? v + "T00:00:00" : v + (v.endsWith("Z") ? "" : "Z"));
  if (isNaN(d.getTime())) return v;
  return v.length === 10
    ? d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const label = (s: string | null | undefined): string => (s ? s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "—");
