import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Plus, Trash2 } from "lucide-react";
import { del, get, patch, post } from "../api/client";
import type { Metric, MetricEntry } from "../api/types";
import MetricChart from "../components/MetricChart";
import { ConfirmButton, Empty, Modal, PageHeader, Spinner } from "../components/ui";
import { fmtDate, todayValue } from "../lib/format";
import { toast } from "../lib/toast";

interface CsvPreview {
  format: string;
  rows: number;
  preview: { line: number; date: string; metric: string; value: number; note: string | null }[];
  errors: { line: number; error: string; raw?: string }[];
  error_count: number;
  new_metrics: string[];
  inserted: number;
  updated: number;
  dry_run: boolean;
}

function EntryRow({ e, m, onChanged }: { e: MetricEntry; m: Metric; onChanged: () => void }) {
  const [value, setValue] = useState(String(e.value));
  const [note, setNote] = useState(e.note ?? "");
  useEffect(() => {
    setValue(String(e.value));
    setNote(e.note ?? "");
  }, [e.value, e.note]);
  const save = () => {
    const v = Number(value);
    if (isNaN(v) || (v === e.value && note === (e.note ?? ""))) return;
    post("/metrics/entries", { metric_id: m.id, date: e.date, value: v, note: note || null })
      .then(onChanged)
      .catch((err: Error) => toast.error(err.message));
  };
  return (
    <tr className="border-t border-border/60">
      <td className="px-3 py-1.5 tabular-nums">{fmtDate(e.date)}</td>
      <td className="px-3 py-1.5">
        <input className="input w-24 py-0.5 tabular-nums" value={value} onChange={(x) => setValue(x.target.value)} onBlur={save} onKeyDown={(x) => x.key === "Enter" && save()} />
        <span className="ml-1 text-xs text-muted">{m.unit}</span>
      </td>
      <td className="px-3 py-1.5">
        <input className="input w-full py-0.5" value={note} onChange={(x) => setNote(x.target.value)} onBlur={save} placeholder="—" />
      </td>
      <td className="px-3 py-1.5 text-right">
        <ConfirmButton
          className="btn btn-sm btn-ghost text-danger"
          confirmText="Delete?"
          onConfirm={() =>
            del(`/metrics/entries/${e.id}`)
              .then(onChanged)
              .catch((err: Error) => toast.error(err.message))
          }
        >
          <Trash2 size={13} />
        </ConfirmButton>
      </td>
    </tr>
  );
}

function CsvImport({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const send = async (f: File, dry: boolean) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch(`/api/metrics/import-csv?dry_run=${dry}`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? res.statusText);
      if (dry) setPreview(data);
      else {
        toast.success(`Imported: ${data.inserted} new, ${data.updated} updated${data.error_count ? `, ${data.error_count} rows skipped` : ""}`);
        onDone();
        onClose();
        setPreview(null);
        setFile(null);
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="Import measurements from CSV" width="max-w-3xl">
      <p className="text-sm text-muted">
        Two layouts work. <b>Long</b>: columns <code>date, metric, value, note</code>. <b>Wide</b>: <code>date</code> plus one column per metric, e.g.{" "}
        <code>date, Weight, Body fat, Waist (in)</code>. Dates as 2024-03-15 or 03/15/2024. Existing values on the same date are overwritten.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn" onClick={() => input.current?.click()}>
          <FileUp size={14} /> Choose CSV
        </button>
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv,.txt"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) {
              setFile(f);
              send(f, true);
            }
          }}
        />
        <span className="text-sm">{file?.name}</span>
        <a className="btn btn-ghost btn-sm ml-auto" href="/api/metrics/sample-csv?format=long">
          <Download size={13} /> sample (long)
        </a>
        <a className="btn btn-ghost btn-sm" href="/api/metrics/sample-csv?format=wide">
          <Download size={13} /> sample (wide)
        </a>
      </div>
      {busy && (
        <div className="mt-3 flex items-center gap-2 text-sm text-muted">
          <Spinner /> reading…
        </div>
      )}
      {preview && (
        <div className="mt-4 flex flex-col gap-2 text-sm">
          <div>
            Detected <b>{preview.format}</b> format: {preview.rows} values · {preview.inserted} new · {preview.updated} overwrite existing
            {preview.new_metrics.length > 0 && <> · creates metrics: {preview.new_metrics.join(", ")}</>}
          </div>
          {preview.error_count > 0 && (
            <div className="rounded border border-danger/40 bg-danger/5 p-2 text-xs">
              <div className="mb-1 font-medium text-danger">{preview.error_count} rows will be skipped</div>
              {preview.errors.slice(0, 8).map((e) => (
                <div key={e.line}>
                  line {e.line}: {e.error}
                </div>
              ))}
            </div>
          )}
          <div className="max-h-60 overflow-auto rounded border border-border">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel text-left text-muted">
                <tr>
                  <th className="px-2 py-1">line</th>
                  <th className="px-2 py-1">date</th>
                  <th className="px-2 py-1">metric</th>
                  <th className="px-2 py-1">value</th>
                  <th className="px-2 py-1">note</th>
                </tr>
              </thead>
              <tbody>
                {preview.preview.map((r, i) => (
                  <tr key={i} className="border-t border-border/60">
                    <td className="px-2 py-1 text-muted">{r.line}</td>
                    <td className="px-2 py-1">{r.date}</td>
                    <td className="px-2 py-1">{r.metric}</td>
                    <td className="px-2 py-1 tabular-nums">{r.value}</td>
                    <td className="px-2 py-1 text-muted">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex justify-end">
            <button className="btn btn-primary" disabled={busy || preview.rows === 0 || !file} onClick={() => file && send(file, false)}>
              Import {preview.rows} values
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function Stats() {
  const qc = useQueryClient();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => get<Metric[]>("/metrics") });
  const presets = useQuery({ queryKey: ["metrics", "presets"], queryFn: () => get<{ name: string; unit: string; kind: string; decimals: number }[]>("/metrics/presets") });
  const [activeId, setActiveId] = useState<number | null>(null);
  const active = metrics.data?.find((m) => m.id === activeId) ?? metrics.data?.[0];
  const entries = useQuery({
    queryKey: ["metrics", "entries", active?.id],
    queryFn: () => get<MetricEntry[]>("/metrics/entries", { metric_id: active!.id }),
    enabled: !!active,
  });
  const [date, setDate] = useState(todayValue());
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [csv, setCsv] = useState(false);
  const [custom, setCustom] = useState({ name: "", unit: "" });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["metrics"] });
    qc.invalidateQueries({ queryKey: ["sequence"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const add = () => {
    if (!active || value === "" || isNaN(Number(value))) return;
    post("/metrics/entries", { metric_id: active.id, date, value: Number(value), note: note || null })
      .then(() => {
        setValue("");
        setNote("");
        refresh();
      })
      .catch((e: Error) => toast.error(e.message));
  };
  const createMetric = (body: { name: string; unit: string; kind?: string; decimals?: number }) =>
    post<Metric>("/metrics", body)
      .then((m) => {
        refresh();
        setActiveId(m.id);
      })
      .catch((e: Error) => toast.error(e.message));

  if (metrics.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Spinner /> loading…
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Stats" subtitle="Measurements show up under the matching photos in the Viewer and in exported captions.">
        <button className="btn" onClick={() => setCsv(true)}>
          <FileUp size={14} /> Import CSV
        </button>
      </PageHeader>
      <div className="flex max-w-5xl flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center gap-1 border-b border-border">
          {metrics.data!.map((m) => (
            <button
              key={m.id}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${m.id === active?.id ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}
              onClick={() => setActiveId(m.id)}
            >
              {m.name} <span className="text-xs text-muted">{m.entry_count ?? 0}</span>
            </button>
          ))}
          <div className="ml-auto flex flex-wrap items-center gap-1 pb-1">
            {(presets.data ?? []).map((p) => (
              <button key={p.name} className="btn btn-ghost btn-sm" onClick={() => createMetric(p)} title={`add ${p.name} (${p.unit})`}>
                <Plus size={12} /> {p.name}
              </button>
            ))}
          </div>
        </div>

        {active ? (
          <>
            <div className="card flex flex-wrap items-end gap-2 p-3">
              <label className="flex flex-col gap-1">
                <span className="label">Date</span>
                <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label">
                  {active.name} ({active.unit || "no unit"})
                </span>
                <input
                  className="input w-32 tabular-nums"
                  inputMode="decimal"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && add()}
                  autoFocus
                />
              </label>
              <label className="flex flex-1 flex-col gap-1">
                <span className="label">Note</span>
                <input className="input" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              </label>
              <button className="btn btn-primary" onClick={add} disabled={value === ""}>
                <Plus size={14} /> Add
              </button>
            </div>

            <div className="card p-3">
              <MetricChart
                title={`${active.name}${active.unit ? ` (${active.unit})` : ""}`}
                points={(entries.data ?? []).map((e) => ({ date: e.date, value: e.value }))}
                unit={active.unit}
                decimals={active.decimals}
                height={220}
              />
            </div>

            <div className="card overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm">
                <span className="font-medium">Entries</span>
                {!active.is_builtin && (
                  <>
                    <input
                      className="input ml-auto w-20 py-0.5 text-xs"
                      defaultValue={active.unit}
                      onBlur={(e) => e.target.value !== active.unit && patch(`/metrics/${active.id}`, { unit: e.target.value }).then(refresh)}
                      title="unit"
                    />
                    <ConfirmButton
                      className="btn btn-sm btn-danger"
                      confirmText={`Delete ${active.name} and all entries?`}
                      onConfirm={() =>
                        del(`/metrics/${active.id}`)
                          .then(() => {
                            setActiveId(null);
                            refresh();
                          })
                          .catch((e: Error) => toast.error(e.message))
                      }
                    >
                      <Trash2 size={13} /> metric
                    </ConfirmButton>
                  </>
                )}
                {active.is_builtin && <span className="ml-auto text-xs text-muted">units follow Settings (lb / kg)</span>}
              </div>
              {(entries.data ?? []).length === 0 ? (
                <div className="px-3 py-6 text-sm text-muted">No entries yet.</div>
              ) : (
                <table className="w-full text-sm">
                  <tbody>
                    {entries.data!.map((e) => (
                      <EntryRow key={e.id} e={e} m={active} onChanged={refresh} />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        ) : (
          <Empty title="No metrics" />
        )}

        <div className="card flex flex-wrap items-end gap-2 p-3">
          <span className="label w-full">Custom metric</span>
          <input className="input w-48" placeholder="name, e.g. Thigh" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
          <input className="input w-24" placeholder="unit" value={custom.unit} onChange={(e) => setCustom({ ...custom, unit: e.target.value })} />
          <button
            className="btn"
            disabled={!custom.name.trim()}
            onClick={() => createMetric({ name: custom.name.trim(), unit: custom.unit.trim() }).then(() => setCustom({ name: "", unit: "" }))}
          >
            <Plus size={14} /> Add metric
          </button>
        </div>
      </div>
      <CsvImport open={csv} onClose={() => setCsv(false)} onDone={refresh} />
    </div>
  );
}
