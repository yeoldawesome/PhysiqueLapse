import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router";
import { AlertTriangle, Plus, RefreshCw, Upload, X } from "lucide-react";
import { api, del, get } from "../api/client";
import { keys, useCategories, useCategoryMutations, usePatchPhoto, useRunJob } from "../api/queries";
import { toast } from "../lib/toast";
import { ConfirmButton, Spinner } from "./ui";

type Example = {
  kind: "photo" | "upload";
  id: number;
  pose_category_id: number;
  looks_like: number | null;
  agrees: boolean | null;
  no_pose?: boolean;
  thumb_url: string;
  flip_horizontal: boolean;
  title: string;
};

const MIN_EXAMPLES = 2;
const EXAMPLES_KEY = ["categories", "examples"]; // under "categories" so a pose change anywhere refreshes it

/** "Mirror Selfie - Side (Arm 90) - Left Side" -> "Side 90 L", to fit under a thumbnail */
const short = (n: string) =>
  n
    .replace(/^Mirror Selfie - /, "")
    .replace(/\(Phone in (Left|Right) Hand\)/, "$1 hand")
    .replace(/\(Arm 90\)/, "90")
    .replace(/\(Arm Down\)/, "down")
    .replace(/\((Left|Right) Arm\)/, "$1")
    .replace(/ - (Left|Right)( Side)?$/, " $1")
    .replace(/\bLeft\b/, "L")
    .replace(/\bRight\b/, "R");

/** The images each pose is learned from: library photos you set by hand, plus uploads. A photo
 * goes to the pose whose examples its joints and angles are closest to, so these decide everything. */
export default function PoseTeaching() {
  const qc = useQueryClient();
  const { data: cats = [] } = useCategories();
  const { data: examples, isLoading } = useQuery({ queryKey: EXAMPLES_KEY, queryFn: () => get<Example[]>("/poses/examples") });
  const patch = usePatchPhoto();
  const run = useRunJob();
  const [uploadingFor, setUploadingFor] = useState<number | null>(null);
  const [removed, setRemoved] = useState<Set<string>>(new Set()); // hidden right away, before the list reloads
  const hide = (key: string) => setRemoved((r) => new Set(r).add(key));
  const fileInput = useRef<HTMLInputElement>(null);
  const pendingCat = useRef<number | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.categories });
    qc.invalidateQueries({ queryKey: EXAMPLES_KEY });
  };
  const upload = useMutation({
    mutationFn: async ({ cid, files }: { cid: number; files: File[] }) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f, f.name));
      return api<{ added: number[]; rejected: { file: string; reason: string }[] }>(`/poses/${cid}/examples`, { method: "POST", body: fd });
    },
    onSuccess: (r) => {
      if (r.added.length) toast.info(`${r.added.length} example${r.added.length === 1 ? "" : "s"} added`);
      r.rejected.forEach((x) => toast.error(`${x.file}: ${x.reason}`));
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
    onSettled: () => setUploadingFor(null),
  });
  const removeUpload = useMutation({
    mutationFn: (id: number) => del(`/poses/examples/${id}`),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  });

  const pickFiles = (cid: number) => {
    pendingCat.current = cid;
    fileInput.current?.click();
  };
  const onFiles = (list: FileList | null) => {
    const cid = pendingCat.current;
    if (!list || !list.length || cid == null) return;
    setUploadingFor(cid);
    upload.mutate({ cid, files: Array.from(list) });
    if (fileInput.current) fileInput.current.value = "";
  };

  const name = (id: number | null) => cats.find((c) => c.id === id)?.name ?? "?";

  // new pose: created here or from the Dashboard's "New pose" card (which links to #pose-<id>)
  const { create } = useCategoryMutations();
  const [newName, setNewName] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const hashId = /^#pose-(\d+)$/.exec(location.hash)?.[1];
  const focus = hashId ? Number(hashId) : null;
  const addPose = () => {
    const n = newName.trim();
    if (!n) return;
    create.mutate({ name: n }, { onSuccess: (c) => (setNewName(""), navigate(`/settings#pose-${c.id}`, { replace: true })) });
  };
  useEffect(() => {
    if (focus == null || !examples || !cats.some((c) => c.id === focus)) return;
    document.getElementById(`pose-${focus}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focus, examples, cats]);
  const flagged = (examples ?? []).filter((e) => e.looks_like != null).length;
  const total = (examples ?? []).filter((e) => !e.no_pose).length;

  return (
    <section className="card p-4">
      <input ref={fileInput} type="file" accept="image/*" multiple className="hidden" onChange={(e) => onFiles(e.target.files)} />
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="font-medium">Pose examples</h2>
        <span className="text-xs text-muted">{total} examples</span>
        <span className="flex-1" />
        <input
          className="input w-56"
          placeholder="new pose, e.g. Most Muscular"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addPose()}
        />
        <button className="btn" disabled={!newName.trim() || create.isPending} onClick={addPose} title="add your own pose; then upload a few examples of it">
          <Plus size={14} /> New pose
        </button>
        <ConfirmButton
          className="btn btn-primary"
          confirmText="Reclassify every photo?"
          title="redo the pose of every photo you have not set by hand, using these examples"
          onConfirm={() => run.mutate({ path: "/poses/classify", body: { force: true } })}
        >
          <RefreshCw size={14} /> Reclassify all
        </ConfirmButton>
      </div>
      <p className="mb-3 text-xs text-muted">
        Each photo goes to the pose whose examples its joints and arm angles are closest to. Upload a few clear images of each pose (any photos,
        not only yours), or set a photo's pose in the Library to make it an example. Left and right poses share examples, mirrored, so one side is
        enough. Give every pose at least {MIN_EXAMPLES} examples, then press <b>Reclassify all</b>.
        {flagged > 0 && (
          <>
            {" "}
            <span className="text-warn">{flagged} examples look more like another pose</span> (orange, with that pose's name): check them and remove
            the wrong ones, or they pull look-alike photos the wrong way.
          </>
        )}
      </p>
      {isLoading || !examples ? (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> loading…
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-border/60">
          {cats.map((c) => {
            const ex = examples.filter((e) => e.pose_category_id === c.id && !removed.has(`${e.kind}-${e.id}`));
            const usable = ex.filter((e) => !e.no_pose).length;
            const busy = uploadingFor === c.id;
            return (
              <div key={c.id} id={`pose-${c.id}`} className={`scroll-mt-20 py-2 ${focus === c.id ? "-mx-2 rounded-md bg-accent/10 px-2" : ""}`}>
                <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-muted">
                    {usable} example{usable === 1 ? "" : "s"} · {c.photo_count} photos
                  </span>
                  {usable < MIN_EXAMPLES && c.rule_key !== "other" && (
                    <span className="inline-flex items-center gap-1 text-xs text-warn">
                      <AlertTriangle size={12} /> needs {MIN_EXAMPLES - usable} more
                    </span>
                  )}
                  <span className="flex-1" />
                  <button className="btn btn-sm" disabled={busy} onClick={() => pickFiles(c.id)} title="upload images of this pose">
                    {busy ? <Spinner /> : <Upload size={12} />} Upload examples
                  </button>
                  <Link to={`/library?pose_id=${c.id}`} className="btn btn-sm" title="open this pose in the Library; setting or accepting a photo's pose there makes it an example">
                    pick from library
                  </Link>
                </div>
                {ex.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {ex.map((e) => {
                      const warn = e.looks_like != null;
                      const tip = e.no_pose ? `${e.title}: no person found, teaches nothing` : warn ? `${e.title}: looks more like ${name(e.looks_like)}` : e.title;
                      const img = (
                        <img
                          src={e.thumb_url}
                          alt=""
                          loading="lazy"
                          className={`h-20 w-16 rounded object-cover ${warn ? "ring-2 ring-warn" : e.no_pose ? "border border-border opacity-50" : "border border-border"}`}
                          style={e.flip_horizontal ? { transform: "scaleX(-1)" } : undefined}
                        />
                      );
                      return (
                        <div key={`${e.kind}-${e.id}`} className="group relative" title={tip}>
                          {e.kind === "photo" ? (
                            <Link to={`/library?photo=${e.id}`}>{img}</Link>
                          ) : (
                            <a href={`/api/poses/examples/${e.id}/image`} target="_blank" rel="noreferrer">
                              {img}
                            </a>
                          )}
                          {e.kind === "upload" && <div className="pointer-events-none absolute left-0.5 top-0.5 rounded bg-black/60 px-1 text-[9px] text-white">upload</div>}
                          {warn && (
                            <div className="pointer-events-none absolute inset-x-0 bottom-0 truncate rounded-b bg-black/70 px-1 text-[9px] leading-tight text-warn">
                              ≈ {short(name(e.looks_like))}
                            </div>
                          )}
                          <button
                            className="absolute right-0.5 top-0.5 hidden rounded bg-black/70 p-0.5 text-white group-hover:block"
                            title={e.kind === "photo" ? "remove this example (the photo goes back to automatic)" : "delete this uploaded example"}
                            onClick={() => {
                              hide(`${e.kind}-${e.id}`);
                              if (e.kind === "photo") patch.mutate({ id: e.id, body: { clear_pose: true } });
                              else removeUpload.mutate(e.id);
                            }}
                          >
                            <X size={12} />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
