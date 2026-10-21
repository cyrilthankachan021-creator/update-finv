import { useRef, useState } from "react";
import { uploadFile } from "../api";
import type { Doc } from "../types";
import { BankCheck } from "./BankCheck";
import { Badge, Icon } from "./ui";
import { useUnsavedChanges } from "./Unsaved";

export interface KindOption { value: string; label: string }

const EXT = [".csv", ".xlsx", ".xml", ".pdf", ".jpg", ".jpeg", ".png"];
const DOC_ONLY = new Set(["purchase_invoice", "sales_invoice", "credit_note", "debit_note"]);

interface Item { id: string; name: string; pct: number; state: "uploading" | "done" | "failed" | "cancelled"; doc?: Doc; error?: string; file: File; cancel?: () => void }

export function UploadArea({ companyId, workspace, kinds, title, subtitle, extensions = EXT, extraFields, onUploaded }: {
  companyId: number; workspace: "accounting" | "audit"; kinds: KindOption[]; title: string; subtitle: string;
  extensions?: string[]; extraFields?: () => Record<string, string>; onUploaded: (doc: Doc) => void;
}) {
  const [kind, setKind] = useState(kinds[0].value);
  const [drag, setDrag] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const maxMb = 20;
  const prompt = useUnsavedChanges(items.some((x) => x.state === "uploading"));   // leaving would abandon the upload

  const update = (id: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const start = (file: File, existingId?: string) => {
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!extensions.includes(ext)) { setMsg(`${file.name}: this file type is not supported. Use ${extensions.join(", ")}.`); return; }
    if (file.size > maxMb * 1024 * 1024) { setMsg(`${file.name} is larger than ${maxMb} MB.`); return; }
    if ([".pdf", ".jpg", ".jpeg", ".png"].includes(ext) && !DOC_ONLY.has(kind)) {
      setMsg("For PDF and image files, choose the document type (purchase invoice, sales invoice, credit note or debit note)."); return;
    }
    setMsg(null);
    const id = existingId ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    form.append("workspace", workspace);
    Object.entries(extraFields?.() ?? {}).forEach(([k, v]) => v && form.append(k, v));
    const key = `${id}-${kind}`;
    const { promise, cancel } = uploadFile(`/api/companies/${companyId}/documents`, form, (pct) => update(id, { pct }), key);
    const item: Item = { id, name: file.name, pct: 0, state: "uploading", file, cancel };
    setItems((xs) => (existingId ? xs.map((x) => (x.id === id ? item : x)) : [item, ...xs]));
    promise.then((d) => { update(id, { state: "done", doc: d as Doc, pct: 100 }); onUploaded(d as Doc); })
      .catch((e: Error & { status?: number }) => update(id, { state: e.status === -1 ? "cancelled" : "failed", error: e.message }));
  };

  const onFiles = (files: FileList | null) => { if (files) Array.from(files).forEach((f) => start(f)); };

  return (
    <div className="stack-sm">
      {prompt}
      <div className={`dropzone ${drag ? "drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}>
        <div className="faint" style={{ marginBottom: 8 }}><Icon name="upload" size={24} /></div>
        <h3>{title}</h3>
        <p className="muted">{subtitle}</p>
        <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
          <label className="sr-only" htmlFor={`kind-${workspace}`}>Document type</label>
          <select id={`kind-${workspace}`} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Document type">
            {kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
          <button className="btn primary" onClick={() => input.current?.click()}>Choose files</button>
          <input ref={input} type="file" multiple hidden accept={extensions.join(",")} data-testid="file-input"
            onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
        </div>
        <p className="faint" style={{ marginTop: 10, fontSize: 12 }}>
          {extensions.map((e) => e.slice(1).toUpperCase()).filter((v, i, a) => a.indexOf(v) === i && v !== "JPEG").join(", ")} · up to {maxMb} MB · or drag files here
        </p>
      </div>
      {msg && <div className="notice warn" role="alert"><Icon name="alert" /><div>{msg}</div></div>}
      {items.length > 0 && (
        <ul className="checks" aria-label="Uploads">
          {items.map((it) => (
            <li key={it.id}>
              <div className="grow stack-sm">
                <div className="row-between">
                  <strong>{it.name}</strong>
                  {it.state === "uploading" && <span className="row"><span className="muted">{it.pct}%</span><button className="btn small" onClick={() => it.cancel?.()}>Cancel</button></span>}
                  {it.state === "done" && it.doc && <Badge status={it.doc.status} />}
                  {(it.state === "failed" || it.state === "cancelled") && <span className="row"><Badge status={it.state === "failed" ? "failed" : "rejected"}>{it.state === "failed" ? "Failed" : "Cancelled"}</Badge><button className="btn small" onClick={() => start(it.file, it.id)}>Retry</button></span>}
                </div>
                {it.state === "uploading" && <div className="progress" aria-label={`Upload progress ${it.pct}%`}><div style={{ width: `${it.pct}%` }} /></div>}
                {it.state === "done" && it.doc && (
                  <span className="muted">
                    {it.doc.status === "failed" ? it.doc.error :
                      it.doc.status === "repeated_upload" ? it.doc.notes[0] :
                        `${it.doc.record_count} record${it.doc.record_count === 1 ? "" : "s"} extracted and checked.`}
                  </span>
                )}
                {it.state === "done" && it.doc?.validation && <BankCheck v={it.doc.validation} />}
                {it.error && <span style={{ color: "var(--err)" }}>{it.error}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
