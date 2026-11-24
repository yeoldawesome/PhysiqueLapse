import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowLeft, ArrowRight, Check, FlipHorizontal2, Hand, SkipForward, Sparkles, Upload } from "lucide-react";
import {
  useOnboarding,
  useOnboardingMutations,
  usePoseCandidates,
  useSetupPoses,
  type OnboardingStatus,
  type SetupPose,
} from "../api/onboarding";
import { PageHeader, Spinner } from "../components/ui";
import { toast } from "../lib/toast";

const PICKS_HINT = 3; // examples we suggest per pose
const PAGE = 30;

/** First run: after import, before anything is sorted, the user shows the app their own
 *  version of each pose (or skips the poses they don't do). */
export default function PoseSetup() {
  const { data: st } = useOnboarding();
  return (
    <div>
      <PageHeader title="Show us your poses" subtitle="a one-time step before your photos are sorted" />
      <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
        {!st ? (
          <Spinner />
        ) : st.stage === "import" ? (
          <ImportFirst />
        ) : st.stage === "prepare" ? (
          <Preparing st={st} />
        ) : st.stage === "poses" ? (
          <Wizard st={st} />
        ) : (
          <AllSet />
        )}
      </div>
    </div>
  );
}

function ImportFirst() {
  return (
    <div className="card flex flex-col items-start gap-3 p-6">
      <h2 className="text-lg font-semibold">Import your photos first</h2>
      <p className="text-sm text-muted">Once they're in, the app finds the ones that show your physique and asks you for an example or two of each pose.</p>
      <Link to="/import" className="btn btn-primary">
        <Upload size={14} /> Import photos
      </Link>
    </div>
  );
}

function Preparing({ st }: { st: OnboardingStatus }) {
  const { prepare } = useOnboardingMutations();
  const started = useRef(false);
  useEffect(() => {
    // whatever imported the photos usually queued this already; the server ignores a second request
    if (!started.current) {
      started.current = true;
      prepare.mutate();
    }
  }, [prepare]);
  const total = st.photos;
  const done = total - st.landmarks_pending;
  const reading = st.landmarks_pending === 0;
  const pct = reading ? (100 * st.features_ready) / Math.max(1, st.physique_photos) : (100 * done) / Math.max(1, total);
  return (
    <div className="card flex flex-col gap-3 p-6">
      <div className="flex items-center gap-2">
        <Spinner />
        <h2 className="text-lg font-semibold">{reading ? "Reading your poses" : "Finding your photos with a physique in them"}</h2>
      </div>
      <p className="text-sm text-muted">
        {reading
          ? `${st.features_ready} of ${st.physique_photos} photos`
          : `Looking for a body in every photo: ${done} of ${total}. Photos without one (screenshots, food, scenery) are left out of the next step.`}
      </p>
      <div className="h-2 overflow-hidden rounded-full bg-panel-2">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-muted">This page moves on by itself. You can leave it open or keep using the app.</p>
    </div>
  );
}

function AllSet() {
  return (
    <div className="card flex flex-col items-start gap-3 p-6">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Check className="text-ok" /> Your poses are set
      </h2>
      <p className="text-sm text-muted">
        Photos are sorted with your examples. Fix any that landed in the wrong pose in <b>Library → needs review</b>; add or change examples any time in{" "}
        <b>Settings → Pose examples</b>.
      </p>
      <div className="flex gap-2">
        <Link to="/library?needs_review=1" className="btn">
          Review
        </Link>
        <Link to="/" className="btn btn-primary">
          Dashboard <ArrowRight size={14} />
        </Link>
      </div>
    </div>
  );
}

function Wizard({ st }: { st: OnboardingStatus }) {
  const { data: poses } = useSetupPoses(true);
  const { finish } = useOnboardingMutations();
  const nav = useNavigate();
  // -1 = intro, poses.length = summary
  const [step, setStep] = useState(-1);
  const [skipped, setSkipped] = useState<Set<number>>(() => new Set(st.skipped));
  if (!poses) return <Spinner />;
  const n = poses.length;
  const byId = new Map(poses.map((p) => [p.id, p]));
  const go = (k: number) => {
    setStep(k);
    window.scrollTo({ top: 0 });
  };
  const skip = (id: number, on: boolean) =>
    setSkipped((s) => {
      const t = new Set(s);
      if (on) t.add(id);
      else t.delete(id);
      return t;
    });

  if (step < 0) {
    return (
      <div className="card flex flex-col gap-4 p-6">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/20 text-accent">
          <Hand size={22} />
        </span>
        <h2 className="text-xl font-semibold">{st.physique_photos} of your photos show your physique</h2>
        <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-muted [&_b]:font-medium [&_b]:text-text">
          <p>
            Before sorting them, the app needs to see how <b>you</b> do each pose. Everyone's front double biceps, lat spread or mirror flex looks a bit different,
            and a couple of your own examples beat any generic rule.
          </p>
          <p>
            For each of the {n} poses you'll see your photos that most likely show it. Click <b>1–{PICKS_HINT} clear ones</b>. If you never do a pose, or can't find
            one, press <b>Skip</b>. You can change all of this later in Settings.
          </p>
        </div>
        <div>
          <button className="btn btn-primary" onClick={() => go(0)} autoFocus>
            Start <ArrowRight size={14} />
          </button>
        </div>
      </div>
    );
  }

  if (step >= n) {
    const withEx = poses.filter((p) => p.examples.length + p.uploaded > 0);
    // the app sorts by your examples once at least two poses have some (a left/right pose's
    // examples also count, mirrored, for the other side); before that it uses its built-in rules
    const covered = new Set(withEx.flatMap((p) => (p.mirror_id != null ? [p.id, p.mirror_id] : [p.id])));
    const byExamples = covered.size >= 2;
    const done = async () => {
      if (withEx.length === 0) {
        toast.error("Pick at least one example for one pose, or the app has nothing of yours to sort by.");
        return;
      }
      await finish.mutateAsync(poses.filter((p) => p.examples.length + p.uploaded === 0).map((p) => p.id));
      toast.success("Sorting your photos with your examples. Progress is in the job panel.");
      nav("/");
    };
    return (
      <div className="card flex flex-col gap-4 p-6">
        <h2 className="text-xl font-semibold">Ready to sort</h2>
        <ul className="grid grid-cols-1 gap-1 text-sm md:grid-cols-2">
          {poses.map((p, k) => {
            const count = p.examples.length + p.uploaded;
            return (
              <li key={p.id}>
                <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-panel-2" onClick={() => go(k)}>
                  {count ? <Check size={14} className="text-ok" /> : <SkipForward size={14} className="text-muted" />}
                  <span className="flex-1 truncate">{p.name}</span>
                  <span className="text-xs text-muted">{count ? `${count} example${count > 1 ? "s" : ""}` : "skipped"}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {byExamples ? (
          <p className="text-sm text-muted">
            Every photo goes to the pose whose examples it matches best (photos that match none go to <b>Other</b>), then each pose is lined up. Skipped poses get
            no photos for now; add examples to them later in <b>Settings → Pose examples</b> and press <b>Reclassify all</b>.
          </p>
        ) : (
          <p className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm">
            With examples for only one pose there is nothing to compare against, so the app will sort with its built-in rules instead. Go back and pick an example
            for at least one more pose to sort by yours.
          </p>
        )}
        <div className="flex gap-2">
          <button className="btn" onClick={() => go(n - 1)}>
            <ArrowLeft size={14} /> Back
          </button>
          <button className="btn btn-primary" onClick={done} disabled={finish.isPending}>
            {finish.isPending ? <Spinner size={14} /> : <Sparkles size={14} />} Sort my photos
          </button>
        </div>
      </div>
    );
  }

  const pose = poses[step];
  return (
    <PoseStep
      key={pose.id}
      pose={pose}
      mirror={pose.mirror_id != null ? byId.get(pose.mirror_id) : undefined}
      index={step}
      total={n}
      skipped={skipped.has(pose.id)}
      onSkip={() => {
        skip(pose.id, true);
        go(step + 1);
      }}
      onBack={() => go(step - 1)}
      onNext={() => {
        skip(pose.id, false);
        go(step + 1);
      }}
    />
  );
}

function PoseStep({
  pose,
  mirror,
  index,
  total,
  skipped,
  onSkip,
  onBack,
  onNext,
}: {
  pose: SetupPose;
  mirror?: SetupPose;
  index: number;
  total: number;
  skipped: boolean;
  onSkip: () => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [limit, setLimit] = useState(PAGE);
  const cands = usePoseCandidates(pose.id, limit);
  const { setExample } = useOnboardingMutations();
  const picked = useMemo(() => new Set(pose.examples), [pose.examples]);
  const count = picked.size + pose.uploaded;
  const mirrorCount = mirror ? mirror.examples.length + mirror.uploaded : 0;
  const toggle = (photoId: number) => setExample.mutate({ photoId, poseId: picked.has(photoId) ? null : pose.id });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select")) return;
      if (e.key === "ArrowRight" && count > 0) onNext();
      else if (e.key === "ArrowLeft") onBack();
      else if (e.key.toLowerCase() === "s") onSkip();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, onNext, onBack, onSkip]);

  return (
    <div className="flex flex-col gap-4">
      <div className="card flex flex-col gap-3 p-4">
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">
            Pose {index + 1} of {total}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-2">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${(100 * (index + 1)) / total}%` }} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold">{pose.name}</h2>
            <p className="text-sm text-muted">
              Click {count ? "more of" : `1–${PICKS_HINT} of`} your photos that show this pose clearly. Click again to un-pick.
              {skipped && !count && " (skipped before)"}
            </p>
            {mirror && mirrorCount > 0 && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-muted">
                <FlipHorizontal2 size={13} /> Your {mirrorCount} example{mirrorCount > 1 ? "s" : ""} for “{mirror.name}” already count here, mirrored. Skip if this is just
                the other side.
              </p>
            )}
          </div>
          <span className={`badge ${count ? "bg-ok/15 text-ok" : "bg-panel-2 text-muted"}`}>
            {count} picked{pose.uploaded ? ` (${pose.uploaded} uploaded)` : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-sm" onClick={onBack}>
            <ArrowLeft size={14} /> Back
          </button>
          <button className="btn btn-sm ml-auto" onClick={onSkip} title="S">
            <SkipForward size={14} /> {count ? "Skip" : "I don't have this pose"}
          </button>
          <button className="btn btn-primary btn-sm" onClick={onNext} disabled={count === 0} title="→">
            Next <ArrowRight size={14} />
          </button>
        </div>
      </div>

      {cands.isLoading ? (
        <Spinner />
      ) : !cands.data?.length ? (
        <div className="card p-6 text-sm text-muted">No photos left to choose from for this pose. Skip it.</div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {cands.data.map((c) => {
              const on = picked.has(c.photo_id);
              return (
                <button
                  key={c.photo_id}
                  className={`relative overflow-hidden rounded-md border-2 transition ${on ? "border-accent" : "border-transparent hover:border-border"}`}
                  onClick={() => toggle(c.photo_id)}
                  aria-pressed={on}
                  title={c.taken_at ? new Date(c.taken_at).toLocaleDateString() : undefined}
                >
                  <img src={`/api/photos/${c.photo_id}/thumb`} alt="" loading="lazy" className="aspect-[4/5] w-full object-cover" draggable={false} />
                  {on && (
                    <span className="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded-full bg-accent text-white shadow">
                      <Check size={14} />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {cands.data.length >= limit && (
            <button className="btn self-center" onClick={() => setLimit((l) => l + PAGE)}>
              Show more photos
            </button>
          )}
        </>
      )}
    </div>
  );
}
