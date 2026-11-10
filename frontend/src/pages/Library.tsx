import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { useBulk, useCategories, usePhotos, type PhotoFilters } from "../api/queries";
import PhotoGrid from "../components/PhotoGrid";
import PhotoDrawer from "../components/PhotoDrawer";
import BulkBar from "../components/BulkBar";
import { Empty, PageHeader, Spinner } from "../components/ui";
import { useShortcuts } from "../lib/keyboard";

const PAGE_SIZE = 120;

function readSize(): number {
  try {
    return Number(localStorage.getItem("pl.gridsize") || 168);
  } catch {
    return 168;
  }
}

export default function Library() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [size, setSize] = useState<number>(readSize);
  const { data: cats = [] } = useCategories();
  const bulk = useBulk();

  const filters: PhotoFilters = useMemo(
    () => ({
      pose_id: params.get("pose_id") ?? undefined,
      from: params.get("from") ?? undefined,
      to: params.get("to") ?? undefined,
      source: params.get("source") ?? undefined,
      date_source: params.get("date_source") ?? undefined,
      needs_review: params.get("needs_review") === "1" ? true : undefined,
      flagged: params.get("flagged") === "1" ? true : undefined,
      no_date: params.get("no_date") === "1" ? true : undefined,
      excluded: (params.get("excluded") as PhotoFilters["excluded"]) ?? "all",
      landmarks: params.get("landmarks") ?? undefined,
      q: params.get("q") ?? undefined,
      sort: params.get("sort") ?? "taken_at_desc",
      page: Number(params.get("page") ?? 1),
      page_size: PAGE_SIZE,
    }),
    [params],
  );
  const { data, isLoading, isFetching } = usePhotos(filters);
  const photos = useMemo(() => data?.items ?? [], [data]);

  const set = useCallback(
    (k: string, v: string | undefined | null) => {
      const next = new URLSearchParams(params);
      if (v === undefined || v === null || v === "" || v === "all") next.delete(k);
      else next.set(k, v);
      if (k !== "page") next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  useEffect(() => {
    const t = window.setTimeout(() => {
      if ((params.get("q") ?? "") !== q) set("q", q);
    }, 300);
    return () => window.clearTimeout(t);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try {
      localStorage.setItem("pl.gridsize", String(size));
    } catch {
      /* ignore */
    }
  }, [size]);

  // drop selection entries that are no longer on screen
  useEffect(() => {
    if (selected.size === 0) return;
    const onScreen = new Set(photos.map((p) => p.id));
    const next = new Set([...selected].filter((id) => onScreen.has(id)));
    if (next.size !== selected.size) setSelected(next);
  }, [photos]); // eslint-disable-line react-hooks/exhaustive-deps

  const ids = useMemo(() => [...selected], [selected]);
  const step = useCallback(
    (delta: number) => {
      if (open == null) return;
      const i = photos.findIndex((p) => p.id === open);
      const j = i + delta;
      if (i >= 0 && j >= 0 && j < photos.length) {
        setOpen(photos[j].id);
        setSelected(new Set([photos[j].id]));
      }
    },
    [open, photos],
  );
  useShortcuts(
    useMemo(
      () => ({
        "ctrl+a": (e: KeyboardEvent) => {
          e.preventDefault();
          setSelected(new Set(photos.map((p) => p.id)));
        },
        Escape: () => {
          if (open != null) setOpen(null);
          else setSelected(new Set());
        },
        f: () => {
          if (ids.length) bulk.mutate({ ids, toggle_flip: true });
        },
        ArrowRight: () => step(1),
        ArrowLeft: () => step(-1),
      }),
      [photos, open, ids, bulk, step],
    ),
  );

  const anyFilter = [...params.keys()].some((k) => k !== "sort" && k !== "page");
  const page = filters.page ?? 1;
  const pages = data?.pages ?? 1;

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title="Library" subtitle={data ? `${data.total} photos${isFetching ? " · refreshing" : ""}` : ""}>
          <label className="relative">
            <Search size={14} className="absolute left-2 top-2 text-muted" />
            <input className="input w-48 pl-7" placeholder="filename or notes" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <input
            type="range"
            min={110}
            max={300}
            step={10}
            value={size}
            onChange={(e) => setSize(Number(e.target.value))}
            title="thumbnail size"
            className="w-24"
          />
        </PageHeader>

        <div className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-2 text-xs">
          <select className="select" value={params.get("pose_id") ?? ""} onChange={(e) => set("pose_id", e.target.value)}>
            <option value="">all poses</option>
            <option value="none">unassigned</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.photo_count})
              </option>
            ))}
          </select>
          <input type="date" className="input" value={params.get("from") ?? ""} onChange={(e) => set("from", e.target.value)} />
          <span className="text-muted">to</span>
          <input type="date" className="input" value={params.get("to") ?? ""} onChange={(e) => set("to", e.target.value)} />
          <select className="select" value={params.get("source") ?? ""} onChange={(e) => set("source", e.target.value)}>
            <option value="">any source</option>
            <option value="upload">upload</option>
            <option value="folder">folder</option>
            <option value="snapchat">snapchat</option>
          </select>
          <select className="select" value={params.get("date_source") ?? ""} onChange={(e) => set("date_source", e.target.value)}>
            <option value="">any date source</option>
            <option value="low_confidence">low confidence</option>
            <option value="exif">EXIF</option>
            <option value="snapchat_metadata">Snapchat</option>
            <option value="filename">filename</option>
            <option value="file_mtime">file time</option>
            <option value="manual">manual</option>
          </select>
          <select className="select" value={params.get("landmarks") ?? ""} onChange={(e) => set("landmarks", e.target.value)}>
            <option value="">any landmarks</option>
            <option value="ok">detected</option>
            <option value="pending">pending</option>
            <option value="none">no person</option>
            <option value="failed">failed</option>
          </select>
          <select className="select" value={params.get("excluded") ?? "all"} onChange={(e) => set("excluded", e.target.value)}>
            <option value="all">incl. excluded</option>
            <option value="hide">hide excluded</option>
            <option value="only">only excluded</option>
          </select>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={params.get("needs_review") === "1"} onChange={(e) => set("needs_review", e.target.checked ? "1" : "")} /> needs
            review
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={params.get("flagged") === "1"} onChange={(e) => set("flagged", e.target.checked ? "1" : "")} /> flagged
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={params.get("no_date") === "1"} onChange={(e) => set("no_date", e.target.checked ? "1" : "")} /> no date
          </label>
          <select className="select ml-auto" value={params.get("sort") ?? "taken_at_desc"} onChange={(e) => set("sort", e.target.value)}>
            <option value="taken_at_desc">newest first</option>
            <option value="taken_at_asc">oldest first</option>
            <option value="created_desc">recently added</option>
            <option value="created_asc">first added</option>
            <option value="filename">filename</option>
          </select>
          {anyFilter && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setQ("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              <X size={12} /> reset
            </button>
          )}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Spinner /> loading…
            </div>
          ) : photos.length === 0 ? (
            <Empty title={anyFilter ? "No photos match these filters" : "Your library is empty"}>
              {anyFilter
                ? "Try loosening the filters."
                : "Add photos on the Import page: drag and drop, pick a folder, or point at a folder on this computer."}
            </Empty>
          ) : (
            <PhotoGrid photos={photos} categories={cats} selected={selected} setSelected={setSelected} onOpen={setOpen} size={size} />
          )}
          {pages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-2 text-sm">
              <button className="btn btn-sm" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>
                <ChevronLeft size={14} />
              </button>
              <span className="tabular-nums text-muted">
                page {page} / {pages}
              </span>
              <button className="btn btn-sm" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>

        <BulkBar
          ids={ids}
          categories={cats}
          total={photos.length}
          onSelectAll={() => setSelected(new Set(photos.map((p) => p.id)))}
          onClear={() => setSelected(new Set())}
        />
      </div>
      {open != null && <PhotoDrawer photoId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
