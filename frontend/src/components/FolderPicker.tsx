import { useEffect, useState } from "react";
import { ArrowUp, Folder, HardDrive } from "lucide-react";
import { browse } from "../api/queries";
import type { Browse } from "../api/types";
import { Modal, Spinner } from "./ui";

export default function FolderPicker({
  open,
  initial,
  onClose,
  onPick,
}: {
  open: boolean;
  initial: string;
  onClose: () => void;
  onPick: (path: string) => void;
}) {
  const [path, setPath] = useState(initial);
  const [data, setData] = useState<Browse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async (p: string) => {
    setLoading(true);
    setError(null);
    try {
      const b = await browse(p);
      setData(b);
      setPath(b.path);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) load(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const join = (base: string, name: string) => (base.endsWith("\\") || base.endsWith("/") ? base + name : base + (base.includes("\\") ? "\\" : "/") + name);

  return (
    <Modal open={open} onClose={onClose} title="Choose a folder on this computer" width="max-w-2xl">
      <div className="flex gap-2">
        <input
          className="input flex-1 font-mono text-xs"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") load(path);
          }}
        />
        <button className="btn btn-sm" onClick={() => load(path)}>
          Go
        </button>
        <button className="btn btn-sm" disabled={!data?.parent} onClick={() => data?.parent && load(data.parent)} title="parent folder">
          <ArrowUp size={14} />
        </button>
      </div>
      {data && data.roots.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.roots.map((r) => (
            <button key={r} className="btn btn-sm" onClick={() => load(r)}>
              <HardDrive size={12} /> {r}
            </button>
          ))}
        </div>
      )}
      <div className="mt-2 h-72 overflow-auto rounded border border-border bg-bg">
        {loading && (
          <div className="flex items-center gap-2 p-3 text-sm text-muted">
            <Spinner /> reading…
          </div>
        )}
        {error && <div className="p-3 text-sm text-danger">{error}</div>}
        {data && !loading && (
          <ul className="divide-y divide-border/60">
            {data.dirs.length === 0 && <li className="p-3 text-sm text-muted">no subfolders</li>}
            {data.dirs.map((d) => (
              <li key={d}>
                <button className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-panel-2" onClick={() => load(join(data.path, d))}>
                  <Folder size={14} className="text-accent" /> {d}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3 text-sm">
        <span className="text-muted">
          {data ? `${data.images} images here (subfolders not counted)` : ""}
        </span>
        <button className="btn btn-primary ml-auto" disabled={!data} onClick={() => data && onPick(data.path)}>
          Use this folder
        </button>
      </div>
    </Modal>
  );
}
