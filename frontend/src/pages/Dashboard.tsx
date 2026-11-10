import { Link } from "react-router";
import { Bone, Crosshair, Play, Tags } from "lucide-react";
import { useDashboard, useRunJob } from "../api/queries";
import { Empty, PageHeader, Spinner, Stat } from "../components/ui";
import { daysBetween, fmtDate } from "../lib/format";

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
        <button className="btn" onClick={() => run.mutate({ path: "/poses/classify" })} title="classify poses for photos without a manual assignment">
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
            {data.weight && (
              <Stat
                label={`Weight (${data.weight.unit})`}
                value={data.weight.latest.toFixed(1)}
                sub={`${data.weight.change >= 0 ? "+" : ""}${data.weight.change.toFixed(1)} since ${fmtDate(data.weight.first_date)}`}
              />
            )}
          </div>
        )}

        <div className="card">
          <div className="border-b border-border px-4 py-2 text-sm font-medium">Pose categories</div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Category</th>
                <th className="px-4 py-2 font-medium">Photos</th>
                <th className="px-4 py-2 font-medium">Reference</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {data.categories.map((cat) => (
                <tr key={cat.id} className="border-t border-border/60">
                  <td className="px-4 py-2">
                    <Link to={`/library?pose_id=${cat.id}`} className="hover:underline">
                      {cat.name}
                    </Link>
                  </td>
                  <td className="px-4 py-2 tabular-nums">{cat.photo_count}</td>
                  <td className="px-4 py-2">
                    {cat.reference_photo_id ? (
                      <img src={`/api/photos/${cat.reference_photo_id}/thumb`} className="h-10 w-8 rounded object-cover" alt="" />
                    ) : (
                      <span className="text-xs text-muted">{cat.photo_count ? "none yet (star a photo in the Library)" : "–"}</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {cat.photo_count > 0 && (
                      <span className="inline-flex gap-1">
                        <button className="btn btn-sm" disabled={!cat.reference_photo_id} onClick={() => run.mutate({ path: `/poses/${cat.id}/align` })}>
                          <Crosshair size={12} /> align
                        </button>
                        <Link to={`/viewer?pose_id=${cat.id}`} className="btn btn-sm">
                          <Play size={12} /> view
                        </Link>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
