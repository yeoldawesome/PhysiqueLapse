import { Link } from "react-router";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { Bone, Crosshair, ImageOff, Plus, Tags } from "lucide-react";
import { useCategoryMutations, useDashboard, useRunJob } from "../api/queries";
import PoseActions from "../components/PoseActions";
import { Empty, PageHeader, Spinner, Stat } from "../components/ui";
import { daysBetween, fmtDate } from "../lib/format";

/** The last card in the grid: name a new pose, then go and give it examples. */
function NewPoseCard() {
  const { create } = useCategoryMutations();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const add = () => {
    const n = name.trim();
    if (!n) return;
    create.mutate({ name: n }, { onSuccess: (c) => navigate(`/settings#pose-${c.id}`) });
  };
  if (!editing) {
    return (
      <button
        className="card flex min-h-[16rem] flex-col items-center justify-center gap-2 border-dashed text-muted transition-colors hover:border-accent hover:text-text"
        onClick={() => setEditing(true)}
        title="add your own pose, e.g. Most Muscular"
      >
        <Plus size={28} />
        <span className="text-sm font-medium">New pose</span>
      </button>
    );
  }
  return (
    <div className="card flex min-h-[16rem] flex-col justify-center gap-2 border-dashed p-3">
      <div className="text-sm font-medium">New pose</div>
      <input
        className="input"
        autoFocus
        placeholder="name, e.g. Most Muscular"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") add();
          if (e.key === "Escape") setEditing(false);
        }}
      />
      <div className="text-[11px] text-muted">Next you add a few example photos of it; photos that look like them go here.</div>
      <div className="flex gap-1.5">
        <button className="btn btn-sm btn-primary flex-1 justify-center" disabled={!name.trim() || create.isPending} onClick={add}>
          Add
        </button>
        <button className="btn btn-sm flex-1 justify-center" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** A pose on the home page: its picture first, then the name, then View / Align. */
function PoseCard({ to, image, flip, name, sub, children }: { to: string; image: string | null; flip?: boolean; name: string; sub: string; children: ReactNode }) {
  return (
    <div className="card flex flex-col overflow-hidden">
      <Link to={to} className="group block aspect-[3/4] overflow-hidden bg-black/30" title={`open ${name} in the Library`}>
        {image ? (
          <img
            src={image}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
            style={flip ? { transform: "scaleX(-1)" } : undefined}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-muted">
            <ImageOff size={28} />
          </div>
        )}
      </Link>
      <div className="flex flex-1 flex-col gap-2 p-2.5">
        <div className="min-w-0">
          <Link to={to} className="line-clamp-2 min-h-[2.5rem] text-sm font-medium leading-tight hover:underline" title={name}>
            {name}
          </Link>
          <div className="truncate text-xs text-muted" title={sub}>
            {sub}
          </div>
        </div>
        <div className="mt-auto">{children}</div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { data, isLoading } = useDashboard();
  const run = useRunJob();
  if (isLoading || !data) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Spinner /> loading…
      </div>
    );
  }
  const c = data.counts;
  const span = daysBetween(data.date_span.first, data.date_span.last);
  return (
    <div>
      <PageHeader title="Dashboard">
        <button className="btn" onClick={() => run.mutate({ path: "/landmarks/extract" })} title="detect body landmarks for photos that do not have them yet">
          <Bone size={14} /> Extract landmarks
          {c.landmarks_pending > 0 && <span className="badge bg-accent-2/20 text-accent">{c.landmarks_pending}</span>}
        </button>
        <button className="btn" onClick={() => run.mutate({ path: "/poses/classify" })} title="classify new photos and re-check borderline ones (Settings has Reclassify all)">
          <Tags size={14} /> Classify poses
        </button>
        <button className="btn" onClick={() => run.mutate({ path: "/poses/align-all" })} title="align every category against its reference">
          <Crosshair size={14} /> Align all
        </button>
      </PageHeader>
      <div className="flex flex-col gap-6 p-6">
        {c.photos === 0 ? (
          <Empty title="No photos yet">
            Start on the{" "}
            <Link to="/import" className="text-accent underline">
              Import
            </Link>{" "}
            page. Everything stays on this computer.
          </Empty>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <Stat label="Photos" value={c.photos} sub={c.excluded ? `${c.excluded} excluded` : undefined} />
            <Stat
              label="Date span"
              value={span != null ? `${span} d` : "–"}
              sub={data.date_span.first ? `${fmtDate(data.date_span.first)} → ${fmtDate(data.date_span.last)}` : "no dates yet"}
            />
            <Stat
              label="Unassigned pose"
              value={c.unassigned}
              tone={c.unassigned ? "warn" : undefined}
              sub={
                <Link to="/library?pose_id=none" className="underline">
                  show
                </Link>
              }
            />
            <Stat
              label="Pose needs review"
              value={c.needs_review}
              tone={c.needs_review ? "warn" : undefined}
              sub={
                <Link to="/library?needs_review=1" className="underline">
                  show
                </Link>
              }
            />
            <Stat
              label="Landmarks pending"
              value={c.landmarks_pending}
              sub={
                c.landmarks_missing ? (
                  <Link to="/library?landmarks=none" className="underline">
                    {c.landmarks_missing} without a person
                  </Link>
                ) : undefined
              }
            />
            <Stat label="Aligned" value={c.aligned} tone={c.alignment_stale ? "warn" : undefined} sub={c.alignment_stale ? `${c.alignment_stale} out of date` : undefined} />
            <Stat
              label="Review queue"
              value={c.review_queue}
              tone={c.review_queue ? "warn" : undefined}
              sub={
                <Link to="/review" className="underline">
                  open
                </Link>
              }
            />
            <Stat
              label="No date"
              value={c.no_date}
              tone={c.no_date ? "warn" : undefined}
              sub={
                c.low_confidence_date ? (
                  <Link to="/library?date_source=low_confidence" className="underline">
                    {c.low_confidence_date} low confidence
                  </Link>
                ) : undefined
              }
            />
            <Stat
              label="Not you"
              value={data.people.me_examples ? data.people.not_me : "–"}
              tone={data.people.not_me ? "warn" : undefined}
              sub={
                <Link to="/people" className="underline">
                  {data.people.me_examples ? (data.people.unsure ? `${data.people.unsure} to check · filter people` : "filter people") : "set up filter people"}
                </Link>
              }
            />
            {data.weight && (
              <Stat
                label={`Weight (${data.weight.unit})`}
                value={data.weight.latest.toFixed(1)}
                sub={`${data.weight.change >= 0 ? "+" : ""}${data.weight.change.toFixed(1)} since ${fmtDate(data.weight.first_date)}`}
              />
            )}
          </div>
        )}

        <div>
          <div className="mb-2 text-sm font-medium">Poses</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
            {data.categories.map((cat) => (
              <PoseCard
                key={cat.id}
                to={`/library?pose_id=${cat.id}`}
                image={cat.cover?.thumb_url ?? null}
                flip={cat.cover?.flip_horizontal}
                name={cat.name}
                sub={`${cat.photo_count} photo${cat.photo_count === 1 ? "" : "s"}${cat.photo_count && !cat.reference_photo_id ? " · no reference" : ""}`}
              >
                <PoseActions poseId={cat.id} photoCount={cat.photo_count} hasReference={!!cat.reference_photo_id} stretch />
                {cat.photo_count === 0 && (
                  <Link to={`/settings#pose-${cat.id}`} className="mt-1.5 block text-center text-xs text-accent hover:underline">
                    add examples so photos land here →
                  </Link>
                )}
              </PoseCard>
            ))}
            <NewPoseCard />
            {(["abs", "face"] as const).map((g) => (
              <PoseCard
                key={g}
                to={`/library?pose_id=${g}`}
                image={data[g].cover_url}
                name={g === "abs" ? "Abs" : "Face"}
                sub={`${data[g].visible} photos · close-ups${data[g].unchecked ? ` · ${data[g].unchecked} not checked yet` : ""}`}
              >
                <PoseActions poseId={g} photoCount={data[g].visible} stretch />
              </PoseCard>
            ))}
          </div>
        </div>

        {data.latest.length > 0 && (
          <div>
            <div className="mb-2 text-sm font-medium">Recently added</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {data.latest.map((p) => (
                <Link key={p.id} to={`/library?q=${encodeURIComponent(p.original_filename)}`} className="shrink-0">
                  <img
                    src={p.thumb_url}
                    className="h-28 w-[90px] rounded-md border border-border object-cover"
                    alt=""
                    style={p.flip_horizontal ? { transform: "scaleX(-1)" } : undefined}
                  />
                </Link>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
