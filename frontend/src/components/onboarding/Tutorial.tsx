import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { ArrowLeft, ArrowRight, Crosshair, Film, Hand, ListChecks, Lock, Sparkles, Upload, type LucideIcon } from "lucide-react";
import { useOnboardingMutations } from "../../api/onboarding";

type Slide = { icon: LucideIcon; title: string; body: ReactNode };

const SLIDES: Slide[] = [
  {
    icon: Sparkles,
    title: "Welcome to PhysiqueLapse",
    body: (
      <>
        Turn years of progress photos into one lined-up time-lapse. The app sorts your photos by pose, lines every photo up so your body stays in the same spot,
        and plays them in date order so you can see the change.
      </>
    ),
  },
  {
    icon: Upload,
    title: "1. Bring in your photos",
    body: (
      <>
        Drop photos or whole folders onto the <b>Import</b> page, point it at a folder on disk, or load your Snapchat Memories export. Originals are copied into the
        app; your own files are never changed. The app then finds your joints in every photo and keeps the ones that show your physique.
      </>
    ),
  },
  {
    icon: Hand,
    title: "2. Show it your poses",
    body: (
      <>
        Everyone's front double biceps looks a little different. Before anything is sorted, you pick a few of <b>your own</b> photos for each pose. Don't do a
        pose? Skip it. Your picks are what the app sorts every other photo by.
      </>
    ),
  },
  {
    icon: ListChecks,
    title: "3. Check what it sorted",
    body: (
      <>
        Photos it isn't sure about land in <b>Library → needs review</b>. Accept with <span className="kbd">A</span> or move them to the right pose; every fix makes
        the sorting better.
      </>
    ),
  },
  {
    icon: Crosshair,
    title: "4. Line them up",
    body: <>Each pose has a reference photo. Every other photo is moved, scaled and turned so your shoulders, elbows and hips land where the reference has them.</>,
  },
  {
    icon: Film,
    title: "5. Watch and export",
    body: (
      <>
        Pick a pose in the <b>Viewer</b> and press play, compare two dates side by side, then export an MP4 or GIF. Stats tracks your weight and measurements next to
        the photos.
      </>
    ),
  },
  {
    icon: Lock,
    title: "Private by design",
    body: <>Everything runs on this computer. No accounts, no uploads, no cloud. Your photos never leave this machine.</>,
  },
];

/** First-run walkthrough. Closing it (any way) marks it seen; "Start here" can replay it. */
export default function Tutorial({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [i, setI] = useState(0);
  const nav = useNavigate();
  const { seenTutorial } = useOnboardingMutations();
  if (!open) return null;
  const s = SLIDES[i];
  const last = i === SLIDES.length - 1;
  const close = (to?: string) => {
    seenTutorial.mutate();
    setI(0);
    onClose();
    if (to) nav(to);
  };
  const Icon = s.icon;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Tutorial">
      <div className="card fade-in flex w-full max-w-xl flex-col overflow-hidden shadow-2xl">
        <div className="flex flex-col gap-3 bg-gradient-to-br from-accent/15 via-transparent to-transparent p-6">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent/20 text-accent">
            <Icon size={22} />
          </span>
          <h2 className="text-xl font-semibold">{s.title}</h2>
          <p className="min-h-[96px] text-sm leading-relaxed text-muted [&_b]:font-medium [&_b]:text-text">{s.body}</p>
        </div>
        <div className="flex items-center gap-3 border-t border-border px-6 py-4">
          <div className="flex gap-1.5" aria-label={`step ${i + 1} of ${SLIDES.length}`}>
            {SLIDES.map((_, k) => (
              <button
                key={k}
                className={`h-2 rounded-full transition-all ${k === i ? "w-5 bg-accent" : "w-2 bg-panel-2 hover:bg-muted"}`}
                onClick={() => setI(k)}
                aria-label={`go to step ${k + 1}`}
              />
            ))}
          </div>
          <button className="btn btn-ghost btn-sm ml-auto" onClick={() => close()}>
            Skip tutorial
          </button>
          {i > 0 && (
            <button className="btn btn-sm" onClick={() => setI(i - 1)}>
              <ArrowLeft size={14} /> Back
            </button>
          )}
          {last ? (
            <button className="btn btn-primary btn-sm" onClick={() => close("/import")}>
              Import my photos <ArrowRight size={14} />
            </button>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={() => setI(i + 1)} autoFocus>
              Next <ArrowRight size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
