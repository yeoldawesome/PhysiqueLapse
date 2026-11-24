import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Layers, Sparkles } from "lucide-react";
import { applyGrouping, toStrictness, toThreshold, useGroupingPreview, useGroupingStatus } from "../api/grouping";
import { Spinner } from "./ui";
import { toast } from "../lib/toast";

export default function GroupPhotos({ onApplied }: { onApplied?: () => void }) {
  const qc = useQueryClient();
  const status = useGroupingStatus();
  const [strict, setStrict] = useState(70);
  const [minSize, setMinSize] = useState(3);
  const [debounced, setDebounced] = useState(strict);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status.data?.active) {
      setStrict(toStrictness(status.data.threshold));
      setMinSize(status.data.min_size);
    }
  }, [status.data]);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(strict), 200);
    return () => window.clearTimeout(timer);
  }, [strict]);

  const threshold = toThreshold(debounced);
  const preview = useGroupingPreview(threshold, minSize);
  const data = preview.data;

  const apply = async () => {
    setBusy(true);
    try {
      await applyGrouping(threshold, minSize);
      toast.info("Grouping photos, then aligning each group. Progress is in the job panel.");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["onboarding"] });
      onApplied?.();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <section className="card space-y-3 p-4">
        <p className="text-sm text-muted">
          Photos are compared by how closely their shoulders, elbows, wrists, and hips line up after alignment. Similar photos become an automatic group
          such as “Group 1”; front/back and left/right views stay separate.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">Looser</span>
          <input
            type="range"
            min={0}
            max={100}
            value={strict}
            onChange={(event) => setStrict(Number(event.target.value))}
            className="h-8 min-w-[200px] flex-1"
            aria-label="grouping strictness"
          />
          <span className="text-sm text-muted">Tighter</span>
          <label className="flex items-center gap-1 text-sm">
            min. group size
            <input
              type="number"
              className="input w-16"
              min={1}
              max={50}
              value={minSize}
              onChange={(event) => setMinSize(Math.min(50, Math.max(1, Number(event.target.value) || 1)))}
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {preview.isError ? (
            <span className="text-danger">Could not preview groups: {preview.error.message}</span>
          ) : !data ? (
            <span className="flex items-center gap-2 text-muted">
              <Spinner size={14} /> Comparing photos…
            </span>
          ) : (
            <span>
              <b>{data.groups.length} groups</b> from {data.photos} photos · {data.ungrouped} ungrouped (groups smaller than {data.min_size})
              {data.without_body > 0 && ` · ${data.without_body} without a body`}
              {preview.isFetching && <Spinner size={12} />}
            </span>
          )}
          <button className="btn btn-primary ml-auto" disabled={!data || preview.isError || data.groups.length === 0 || busy} onClick={apply}>
            {busy ? <Spinner size={14} /> : <Sparkles size={14} />} {status.data?.active ? "Regroup photos" : "Create groups"}
          </button>
        </div>
        <p className="text-xs text-muted">This replaces the current pose categories with automatic groups. A database backup is kept before grouping.</p>
      </section>

      {data && data.groups.length === 0 && data.photos === 0 && (
        <div className="card p-4 text-sm text-muted">No photos with detected bodies yet. Run “Extract landmarks” on the Dashboard first.</div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data?.groups.map((group) => (
          <div key={`${group.medoid_photo_id}-${group.size}`} className="card overflow-hidden">
            <div className="flex gap-1 bg-bg p-1">
              {group.sample_urls.slice(0, 4).map((url, index) => (
                <img
                  key={url}
                  src={url}
                  alt=""
                  loading="lazy"
                  className={`aspect-[4/5] min-w-0 flex-1 rounded object-cover ${index === 0 ? "ring-2 ring-accent" : ""}`}
                />
              ))}
            </div>
            <div className="flex items-center gap-2 px-3 py-2 text-sm">
              <Layers size={14} className="text-muted" />
              <span className="min-w-0 flex-1 truncate font-medium">{group.name}</span>
              <span className="text-muted tabular-nums">{group.size}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
