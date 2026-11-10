import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useCategories, useCategoryMutations, useSaveSettings, useSettings, useSystem } from "../api/queries";
import type { Category, Settings as S } from "../api/types";
import { ConfirmButton, Field, PageHeader, Spinner, Toggle } from "../components/ui";
import { fmtBytes } from "../lib/format";

function CategoryRow({ c }: { c: Category }) {
  const { update, remove } = useCategoryMutations();
  const [name, setName] = useState(c.name);
  useEffect(() => setName(c.name), [c.name]);
  const u = (body: Partial<Category>) => update.mutate({ id: c.id, body });
  return (
    <tr className="border-t border-border/60">
      <td className="px-3 py-2">
        <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name !== c.name && u({ name })} />
        <div className="mt-0.5 text-[11px] text-muted">
          {c.photo_count} photos{c.is_builtin ? " · built-in rules" : " · learned from your labels (kNN)"}
        </div>
      </td>
      <td className="px-3 py-2">
        <select className="select" value={c.align_method} onChange={(e) => u({ align_method: e.target.value as Category["align_method"] })}>
          <option value="body">body fit</option>
          <option value="head">pin head</option>
        </select>
      </td>
      <td className="px-3 py-2">
        <select className="select" value={c.anchor_mode} onChange={(e) => u({ anchor_mode: e.target.value as Category["anchor_mode"] })}>
          <option value="auto">auto</option>
          <option value="face">face</option>
          <option value="ears">ears</option>
        </select>
      </td>
      <td className="px-3 py-2">
        <select className="select" value={c.scale_basis} onChange={(e) => u({ scale_basis: e.target.value as Category["scale_basis"] })}>
          <option value="torso">torso</option>
          <option value="nose_hip">nose–hip</option>
          <option value="head">head</option>
        </select>
      </td>
      <td className="px-3 py-2">
        <select className="select" value={c.rotation_basis} onChange={(e) => u({ rotation_basis: e.target.value as Category["rotation_basis"] })}>
          <option value="torso">torso</option>
          <option value="shoulders">shoulders</option>
          <option value="eyes">eyes</option>
          <option value="none">none</option>
        </select>
      </td>
      <td className="px-3 py-2">
        <input
          type="number"
          step="0.05"
          min="0.25"
          max="4"
          className="input w-20"
          defaultValue={c.framing_zoom}
          onBlur={(e) => Number(e.target.value) !== c.framing_zoom && u({ framing_zoom: Number(e.target.value) })}
        />
      </td>
      <td className="px-3 py-2">
        <Toggle checked={c.normalize_facing} onChange={(v) => u({ normalize_facing: v })} />
      </td>
      <td className="px-3 py-2 text-right">
        {c.rule_key !== "other" && (
          <ConfirmButton className="btn btn-sm btn-danger" confirmText="Delete?" onConfirm={() => remove.mutate(c.id)} title="photos move to Other">
            <Trash2 size={13} />
          </ConfirmButton>
        )}
      </td>
    </tr>
  );
}

function NumberField({ label, hint, value, onChange, step = 1, min, max }: { label: string; hint?: string; value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number }) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" className="input" step={step} min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  );
}

export default function Settings() {
  const { data: settings } = useSettings();
  const { data: system } = useSystem();
  const { data: cats = [] } = useCategories();
  const save = useSaveSettings();
  const { create } = useCategoryMutations();
  const [draft, setDraft] = useState<S | null>(null);
  const [newCat, setNewCat] = useState("");
  useEffect(() => {
    if (settings) setDraft(settings);
  }, [settings]);

  if (!draft || !settings) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Spinner /> loading…
      </div>
    );
  }
  const set = <K extends keyof S>(k: K, v: S[K]) => setDraft({ ...draft, [k]: v });
  const changed = (Object.keys(draft) as (keyof S)[]).filter((k) => JSON.stringify(draft[k]) !== JSON.stringify(settings[k]));
  const patch = Object.fromEntries(changed.map((k) => [k, draft[k]])) as Partial<S>;

  return (
    <div>
      <PageHeader title="Settings">
        {changed.length > 0 && <span className="text-xs text-warn">{changed.length} unsaved</span>}
        <button className="btn" disabled={changed.length === 0} onClick={() => setDraft(settings)}>
          Revert
        </button>
        <button className="btn btn-primary" disabled={changed.length === 0 || save.isPending} onClick={() => save.mutate(patch)}>
          Save
        </button>
      </PageHeader>
      <div className="flex max-w-6xl flex-col gap-6 p-6">
        <section className="card p-4">
          <h2 className="mb-3 font-medium">Pose categories</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-1 font-medium">Name</th>
                  <th className="px-3 py-1 font-medium" title="body fit: best overall match of head, shoulders and hips. pin head: the head lands exactly, the torso may drift">Align by</th>
                  <th className="px-3 py-1 font-medium" title="which head points anchor the alignment (pin head mode)">Anchor</th>
                  <th className="px-3 py-1 font-medium">Scale by</th>
                  <th className="px-3 py-1 font-medium">Level by</th>
                  <th className="px-3 py-1 font-medium">Zoom</th>
                  <th className="px-3 py-1 font-medium" title="auto-flip photos that face the other way than the reference">Normalize facing</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cats.map((c) => (
                  <CategoryRow key={c.id} c={c} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex gap-2">
            <input
              className="input w-64"
              placeholder="new category, e.g. Most Muscular"
              value={newCat}
              onChange={(e) => setNewCat(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newCat.trim()) create.mutate({ name: newCat.trim() }, { onSuccess: () => setNewCat("") });
              }}
            />
            <button className="btn" disabled={!newCat.trim()} onClick={() => create.mutate({ name: newCat.trim() }, { onSuccess: () => setNewCat("") })}>
              <Plus size={14} /> Add
            </button>
          </div>
        </section>

        <section className="card grid grid-cols-2 gap-4 p-4 md:grid-cols-4">
          <h2 className="col-span-full font-medium">Alignment</h2>
          <NumberField label="Canvas width" value={draft.canvas_w} onChange={(v) => set("canvas_w", v)} min={256} max={4096} />
          <NumberField label="Canvas height" value={draft.canvas_h} onChange={(v) => set("canvas_h", v)} min={256} max={4096} />
          <NumberField label="Rotation clamp (°)" value={draft.rotation_clamp_deg} step={0.5} onChange={(v) => set("rotation_clamp_deg", v)} />
          <Field label="Background removal" hint="needs the optional rembg extra">
            <Toggle checked={draft.bg_removal} onChange={(v) => set("bg_removal", v)} />
          </Field>
        </section>

        <section className="card grid grid-cols-2 gap-4 p-4 md:grid-cols-4">
          <h2 className="col-span-full font-medium">Pose detection and classification</h2>
          <Field label="Pose model">
            <select className="select" value={draft.pose_model} onChange={(e) => set("pose_model", e.target.value as S["pose_model"])}>
              <option value="heavy">heavy (best)</option>
              <option value="full">full</option>
              <option value="lite">lite (fast)</option>
            </select>
          </Field>
          <NumberField label="Detection input size (px)" value={draft.landmark_input_long_side} step={64} onChange={(v) => set("landmark_input_long_side", v)} />
          <Field label="Flip test-time augmentation" hint="about 2× slower, less jitter">
            <Toggle checked={draft.flip_tta} onChange={(v) => set("flip_tta", v)} />
          </Field>
          <NumberField label="Review below confidence" value={draft.review_confidence} step={0.05} onChange={(v) => set("review_confidence", v)} />
          <NumberField label="kNN k" value={draft.knn_k} onChange={(v) => set("knn_k", v)} />
          <NumberField label="kNN after N labels" value={draft.knn_min_labels} onChange={(v) => set("knn_min_labels", v)} />
          <NumberField label="kNN min categories" value={draft.knn_min_categories} onChange={(v) => set("knn_min_categories", v)} />
        </section>

        <section className="card grid grid-cols-2 gap-4 p-4 md:grid-cols-4">
          <h2 className="col-span-full font-medium">Import and stats</h2>
          <Field label="Skip near-duplicates">
            <Toggle checked={draft.skip_near_duplicates} onChange={(v) => set("skip_near_duplicates", v)} />
          </Field>
          <NumberField label="Near-duplicate distance" hint="perceptual-hash bits (0–64)" value={draft.phash_threshold} onChange={(v) => set("phash_threshold", v)} min={0} max={32} />
          <Field label="Weight units">
            <select className="select" value={draft.units} onChange={(e) => set("units", e.target.value as S["units"])}>
              <option value="lb">lb</option>
              <option value="kg">kg</option>
            </select>
          </Field>
          <NumberField label="Stats window (days)" value={draft.stats_window_days} onChange={(v) => set("stats_window_days", v)} />
          <Field label="Timezone" hint="for Snapchat dates (stored in UTC); 'local' = this computer">
            <input className="input" value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} />
          </Field>
        </section>

        <section className="card grid grid-cols-2 gap-4 p-4 md:grid-cols-4">
          <h2 className="col-span-full font-medium">Snapchat physique filter</h2>
          <NumberField label="Auto-accept ≥" value={draft.filter_accept} step={0.05} onChange={(v) => set("filter_accept", v)} />
          <NumberField label="Auto-reject <" value={draft.filter_reject} step={0.05} onChange={(v) => set("filter_reject", v)} />
          <NumberField label="CLIP weight" value={draft.filter_clip_weight} step={0.05} onChange={(v) => set("filter_clip_weight", v)} />
          <NumberField label="Pose weight" value={draft.filter_pose_weight} step={0.05} onChange={(v) => set("filter_pose_weight", v)} />
          <Field label="Positive prompts (one per line)" className="col-span-2">
            <textarea className="input min-h-[120px] font-mono text-xs" value={draft.clip_positive_prompts.join("\n")} onChange={(e) => set("clip_positive_prompts", e.target.value.split("\n"))} />
          </Field>
          <Field label="Negative prompts (one per line)" className="col-span-2">
            <textarea className="input min-h-[120px] font-mono text-xs" value={draft.clip_negative_prompts.join("\n")} onChange={(e) => set("clip_negative_prompts", e.target.value.split("\n"))} />
          </Field>
        </section>

        {system && (
          <section className="card p-4 text-sm">
            <h2 className="mb-3 font-medium">System</h2>
            <div className="grid grid-cols-1 gap-x-8 gap-y-1 md:grid-cols-2">
              <div>
                <span className="text-muted">Data folder:</span> <span className="font-mono text-xs">{system.data_dir}</span>
              </div>
              <div>
                <span className="text-muted">Data size:</span> {system.data_size_mb} MB · {system.disk_free_gb} GB free
              </div>
              <div>
                <span className="text-muted">Server:</span> {system.bind}
              </div>
              <div>
                <span className="text-muted">ffmpeg:</span> <span className="font-mono text-xs">{system.ffmpeg ?? "not found"}</span>
              </div>
            </div>
            <h3 className="mb-1 mt-4 text-xs font-medium uppercase tracking-wide text-muted">Models</h3>
            <ul className="space-y-0.5">
              {Object.entries(system.models).map(([k, m]) => (
                <li key={k} className="flex gap-2 text-xs">
                  <span className={m.present ? "text-ok" : "text-warn"}>{m.present ? "●" : "○"}</span>
                  <span className="w-24">{k}</span>
                  <span className="text-muted">{m.present ? fmtBytes(m.bytes) : "not downloaded (run backend/scripts/download_models.py)"}</span>
                </li>
              ))}
            </ul>
            <h3 className="mb-1 mt-4 text-xs font-medium uppercase tracking-wide text-muted">Versions</h3>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted">
              {Object.entries(system.versions).map(([k, v]) => (
                <span key={k}>
                  {k} {v ?? "–"}
                </span>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
