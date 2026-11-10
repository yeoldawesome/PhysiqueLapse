import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderOpen, ImagePlus, Upload } from "lucide-react";
import { filesFromDrop, filesFromInput, isImageName, isVideoName, uploadFiles, type PickedFile, type UploadProgress } from "../lib/upload";
import { toast } from "../lib/toast";
import { fmtBytes } from "../lib/format";

export default function Uploader() {
  const qc = useQueryClient();
  const [drag, setDrag] = useState(false);
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);

  const images = picked.filter((p) => isImageName(p.path));
  const videos = picked.filter((p) => isVideoName(p.path)).length;
  const other = picked.length - images.length - videos;
  const bytes = images.reduce((a, p) => a + p.file.size, 0);
  const busy = progress != null && progress.phase !== "done" && progress.phase !== "error";

  const add = (files: PickedFile[]) => {
    if (files.length === 0) return;
    setPicked((prev) => {
      const seen = new Set(prev.map((p) => `${p.path}:${p.file.size}:${p.file.lastModified}`));
      const fresh = files.filter((f) => !seen.has(`${f.path}:${f.file.size}:${f.file.lastModified}`));
      return [...prev, ...fresh];
    });
  };

  const start = async () => {
    if (images.length === 0) return;
    setProgress({ sent: 0, total: images.length, bytesSent: 0, bytesTotal: bytes, phase: "staging" });
    try {
      const r = await uploadFiles(images, setProgress);
      toast.success(`${images.length} files uploaded, importing (job #${r.job.id})`);
      setPicked([]);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["imports"] });
      setTimeout(() => setProgress(null), 1500);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <Upload size={16} className="text-accent" />
        <h2 className="font-medium">Upload from this browser</h2>
        <span className="text-xs text-muted">JPG · PNG · HEIC · WEBP · folders keep their structure</span>
      </div>
      <div
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
          drag ? "border-accent bg-accent/5" : "border-border"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={async (e) => {
          e.preventDefault();
          setDrag(false);
          add(await filesFromDrop(e.dataTransfer));
        }}
      >
        <ImagePlus size={28} className="text-muted" />
        <div className="text-sm">Drop photos or folders here</div>
        <div className="flex gap-2">
          <button className="btn btn-sm" onClick={() => fileInput.current?.click()} disabled={busy}>
            Choose files
          </button>
          <button className="btn btn-sm" onClick={() => dirInput.current?.click()} disabled={busy}>
            <FolderOpen size={14} /> Choose folder
          </button>
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept="image/*,.heic,.heif"
          className="hidden"
          onChange={(e) => {
            add(filesFromInput(e.target.files));
            e.target.value = "";
          }}
        />
        <input
          ref={dirInput}
          type="file"
          multiple
          className="hidden"
          // @ts-expect-error non-standard attribute supported by all major browsers
          webkitdirectory=""
          onChange={(e) => {
            add(filesFromInput(e.target.files));
            e.target.value = "";
          }}
        />
      </div>

      {picked.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <span>
            <b>{images.length}</b> images ({fmtBytes(bytes)})
          </span>
          {videos > 0 && <span className="text-muted">{videos} videos skipped</span>}
          {other > 0 && <span className="text-muted">{other} other files skipped</span>}
          <button className="btn btn-primary ml-auto" disabled={images.length === 0 || busy} onClick={start}>
            <Upload size={14} /> Upload {images.length} images
          </button>
          <button className="btn btn-ghost" disabled={busy} onClick={() => setPicked([])}>
            clear
          </button>
        </div>
      )}

      {progress && (
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-xs text-muted">
            <span>
              {progress.phase === "staging" && `Uploading ${progress.sent}/${progress.total} files`}
              {progress.phase === "committing" && "Starting import…"}
              {progress.phase === "done" && "Uploaded. Import running in the background."}
              {progress.phase === "error" && <span className="text-danger">{progress.error}</span>}
            </span>
            <span className="tabular-nums">{progress.bytesTotal > 0 ? `${Math.round((progress.bytesSent / progress.bytesTotal) * 100)}%` : ""}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-border">
            <div
              className={`h-full ${progress.phase === "error" ? "bg-danger" : "bg-accent-2"}`}
              style={{ width: `${progress.bytesTotal > 0 ? (progress.bytesSent / progress.bytesTotal) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
