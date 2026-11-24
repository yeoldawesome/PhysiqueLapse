import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Check,
  Crosshair,
  Film,
  Images,
  LayoutDashboard,
  ListChecks,
  Lock,
  Play,
  ScanFace,
  Settings as SettingsIcon,
  Shirt,
  Tags,
  Upload,
  UserX,
  Wand2,
} from "lucide-react";
import { useDashboard } from "../api/queries";
import { useGroupingStatus } from "../api/grouping";
import { PageHeader } from "../components/ui";
import Tutorial from "../components/onboarding/Tutorial";

type Step = { title: string; body: ReactNode; done: boolean | null; to: string; action: string };

function Checklist({ steps }: { steps: Step[] }) {
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <div className="card p-5">
      <div className="mb-1 flex items-center gap-3">
        <h2 className="text-base font-semibold">Quick start</h2>
        <span className="text-xs text-muted">
          {doneCount} of {steps.length} done in your library
        </span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-2">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${(100 * doneCount) / steps.length}%` }} />
        </div>
      </div>
      <p className="mb-4 text-sm text-muted">The order that gets you from a pile of photos to a lined-up time-lapse. Ticks update from your actual library.</p>
      <ol className="flex flex-col gap-2">
        {steps.map((s, i) => (
          <li key={s.title} className={`flex gap-3 rounded-md border p-3 ${s.done ? "border-border/60 bg-panel-2/30" : "border-border"}`}>
            <span
              className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                s.done ? "bg-ok/20 text-ok" : "bg-accent/20 text-accent"
              }`}
            >
              {s.done ? <Check size={14} /> : i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className={`text-sm font-medium ${s.done ? "text-muted" : ""}`}>{s.title}</div>
              <div className="text-sm text-muted">{s.body}</div>
            </div>
            <Link to={s.to} className={`btn btn-sm shrink-0 self-center ${s.done ? "" : "btn-primary"}`}>
              {s.action} <ArrowRight size={12} />
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Feature({ icon: Icon, title, to, action, children }: { icon: typeof Play; title: string; to?: string; action?: string; children: ReactNode }) {
  return (
    <div className="card flex flex-col gap-2 p-4">
      <div className="flex items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent/15 text-accent">
          <Icon size={16} />
        </span>
        <h3 className="font-medium">{title}</h3>
      </div>
      <div className="flex-1 text-sm leading-relaxed text-muted [&_b]:font-medium [&_b]:text-text [&_li]:ml-4 [&_li]:list-disc [&_ul]:mt-1 [&_ul]:space-y-0.5">{children}</div>
      {to && (
        <Link to={to} className="btn btn-sm mt-1 self-start">
          {action ?? "Open"} <ArrowRight size={12} />
        </Link>
      )}
    </div>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {sub && <p className="text-sm text-muted">{sub}</p>}
      </div>
      {children}
    </section>
  );
}

const Kbd = ({ children }: { children: ReactNode }) => <span className="kbd">{children}</span>;

export default function Guide() {
  const { data: dash } = useDashboard();
  const c = dash?.counts;
  const known = !!dash;
  const { data: grouping } = useGroupingStatus();
  const [tour, setTour] = useState(false);
  const steps: Step[] = [
    {
      title: "Import your photos",
      body: "Drop photos or a whole folder in, or point at a folder on disk, or upload your Snapchat export. Originals are copied into the app; your files are never changed.",
      done: known ? (c?.photos ?? 0) > 0 : null,
      to: "/import",
      action: "Import",
    },
    {
      title: "Group photos automatically",
      body: (
        <>
          After import, the app detects body landmarks and groups photos that align closely. Groups are labeled <b>Group 1</b>, <b>Group 2</b>, and so on;
          you do not need to name poses or choose example photos.
        </>
      ),
      done: grouping?.active ?? null,
      to: "/grouping",
      action: "Preview groups",
    },
    {
      title: "Review your groups",
      body: (
        <>
          Open the <b>Library</b> to review photos and move any into a different group. Import review items appear after processing is complete.
        </>
      ),
      done: known ? (c?.photos ?? 0) > 0 && (c?.needs_review ?? 1) === 0 : null,
      to: "/library?needs_review=1",
      action: "Review",
    },
    {
      title: "Line them up",
      body: (
        <>
          Press <b>Align all</b> on the Dashboard (or <b>Align</b> on one group). Each photo is moved, scaled and turned so your body lands in the same spot as
          the group's reference photo.
        </>
      ),
      done: known ? (c?.aligned ?? 0) > 0 && (c?.alignment_stale ?? 1) === 0 : null,
      to: "/",
      action: "Dashboard",
    },
    {
      title: "Watch and export",
      body: (
        <>
          Pick a pose in the <b>Viewer</b> and press play. When you like it, press <b>Export</b> for an MP4 or GIF with your own speed, shape and captions.
        </>
      ),
      done: null,
      to: "/viewer",
      action: "Viewer",
    },
  ];

  return (
    <div>
      <PageHeader title="Start here" subtitle="how PhysiqueLapse works and what everything does">
        <button className="btn btn-sm" onClick={() => setTour(true)}>
          <Play size={14} /> Replay tutorial
        </button>
      </PageHeader>
      <Tutorial open={tour} onClose={() => setTour(false)} />
      <div className="mx-auto flex max-w-6xl flex-col gap-8 p-3 md:p-6">
        {/* intro */}
        <div className="card overflow-hidden">
          <div className="flex flex-col gap-3 bg-gradient-to-br from-accent/15 via-transparent to-transparent p-3 md:p-6">
            <div className="flex items-center gap-2 text-accent">
              <BookOpen size={18} />
              <span className="text-xs font-semibold uppercase tracking-wide">Welcome</span>
            </div>
            <h1 className="text-2xl font-semibold leading-tight">Turn years of progress photos into one lined-up time-lapse.</h1>
            <p className="max-w-3xl text-sm leading-relaxed text-muted">
              PhysiqueLapse sorts your photos by pose (front double bicep, mirror-selfie flex, side shots and any poses you add), lines every photo up so your body
              stays in the same place, and plays them in date order so you can actually see the change. It also tracks your weight and measurements, estimates
              body sizes from the photos, and exports videos to share.
            </p>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Lock size={13} className="text-ok" />
              Everything runs on this computer. Photos never leave it, there are no accounts, and the app only talks to itself (127.0.0.1).
            </div>
          </div>
        </div>

        <Checklist steps={steps} />

        <Section title="Every page" sub="What each item in the sidebar is for.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <Feature icon={LayoutDashboard} title="Dashboard" to="/" action="Open Dashboard">
              Your home screen: counts at the top (photos, date span, poses needing review, photos not of you), then a card per group with its picture and{" "}
              <b>View</b> / <b>Align</b> buttons. <b>Process everything</b> detects landmarks, groups photos automatically, and aligns each group.
            </Feature>
            <Feature icon={Images} title="Library" to="/library" action="Open Library">
              Every photo, with filters (pose, dates, source, clothing, needs review and more).
              <ul>
                <li>
                  Click a photo for its <b>drawer</b>: change the date, flip it, set its pose, see its joints and body sizes, mark abs / face / sleeves by hand.
                </li>
                <li>
                  Select many (click, <Kbd>Shift</Kbd>-click, <Kbd>Ctrl</Kbd>+<Kbd>A</Kbd>) for the <b>bulk bar</b>: set pose, accept poses, shift dates, exclude or
                  delete.
                </li>
                <li>Opened from a pose card, it shows that pose with View / Align at the top.</li>
              </ul>
            </Feature>
            <Feature icon={Upload} title="Import" to="/import" action="Open Import">
              Add photos by drag-and-drop, by folder path (with subfolders), or by uploading the zip files of a <b>Snapchat "My Data" export</b>. Dates come from the
              photo's EXIF, its file name or the file time, and exact or near duplicates are skipped. Snapchat imports run a physique filter that keeps lifting photos
              and drops memes and screenshots.
            </Feature>
            <Feature icon={ListChecks} title="Review queue" to="/review" action="Open Review queue">
              Snapchat images the physique filter wasn't sure about. Accept (<Kbd>A</Kbd>) or reject (<Kbd>R</Kbd>) each one; <Kbd>U</Kbd> undoes. The score chart
              helps you pick better cut-offs.
            </Feature>
            <Feature icon={UserX} title="Filter people" to="/people" action="Open Filter people">
              Finds photos that aren't of you. Scan once, pick photos of you, then check <b>Doesn't look like you</b>: mark strangers <b>Not me</b> and their other
              photos are found too. <b>Delete all not-you</b> only removes photos you confirmed (a cap over your eyes or a phone in front of your face often makes
              you look unlike yourself, so nothing is deleted on a guess).
            </Feature>
            <Feature icon={Play} title="Viewer" to="/viewer" action="Open Viewer">
              Plays one pose as a time-lapse, aligned and in date order.
              <ul>
                <li>
                  Modes: <b>single</b>, <b>onion</b> (<Kbd>O</Kbd>, see-through over a comparison photo) and <b>split</b> (<Kbd>S</Kbd>, drag the divider).
                </li>
                <li>
                  <b>Pose match</b> hides photos whose arms are too far from the reference; <b>Hide sleeves</b> hides photos where clothing covers your arms.
                </li>
                <li>Your weight / measurements around each photo's date show under it.</li>
              </ul>
            </Feature>
            <Feature icon={Film} title="Export" to="/viewer" action="Open Viewer">
              The <b>Export</b> button in the Viewer makes an MP4 or GIF with a live preview: speed (or total length), frame rate, hold on the first / last photo,
              crossfade, boomerang, one photo per day / week / month, shape (original, square, 4:5, 9:16 story, 16:9), title, date / day number / stats captions and
              a progress bar. Your settings are remembered; finished exports play right in the list.
            </Feature>
            <Feature icon={Activity} title="Stats" to="/stats" action="Open Stats">
              <b>Tape</b>: log weight and tape measurements (or import a CSV); they appear under photos in the Viewer and in export captions. <b>From photos</b>:
              shoulder width, waist, V-taper, arm size and more estimated from your photos, with trend lines (in inches once you've logged a few tape measurements).
            </Feature>
            <Feature icon={SettingsIcon} title="Settings" to="/settings" action="Open Settings">
              Rename or delete groups and choose how each one is aligned; adjust alignment, detection, and import options.
            </Feature>
          </div>
        </Section>

        <Section title="Built in" sub="Things that work across the app.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <Feature icon={Tags} title="Automatic pose groups" to="/grouping" action="Adjust grouping">
              Grouping prioritizes shoulders, elbows, and wrists; hips have little influence and leg positions are ignored. Groups have generic names such as
              <b> Group 1</b>; photos without a detected body or in groups below the minimum size remain <b>Ungrouped</b>.
            </Feature>
            <Feature icon={Crosshair} title="Alignment">
              Photos are only moved, scaled and turned, never stretched, so sizes stay honest. Fine-tune any photo in the <b>alignment editor</b> (from the drawer or
              the Viewer): drag to move, <Kbd>Q</Kbd>/<Kbd>E</Kbd> to turn, ghost of the reference with <Kbd>G</Kbd>.
            </Feature>
            <Feature icon={Wand2} title="Abs and Face groups">
              Two extra groups across all poses: <b>Abs</b> (photos where your abs show, as aligned torso close-ups) and <b>Face</b> (your face lined up by eyes and
              mouth).               They're checked automatically; fix any photo with the switches in its drawer.
            </Feature>
            <Feature icon={Shirt} title="Sleeves check">
              Every photo is checked for clothing covering the arms, so arm time-lapses can hide sleeved photos (Viewer, exports and Library filter).
            </Feature>
            <Feature icon={ScanFace} title="Background jobs">
              Long tasks (importing, finding joints, classifying, aligning, exporting, scanning faces) run in the background. Watch them in the job panel in the
              bottom-right corner; you can keep using the app meanwhile.
            </Feature>
            <Feature icon={Lock} title="Private by design">
              Photos, models and the database live in the app's <code>data</code> folder on this PC. Deleted photos go to its trash folder and are never re-imported.
            </Feature>
          </div>
        </Section>

        <Section title="Tips">
          <div className="card divide-y divide-border/60">
            {[
              [
                "Photos are in different groups than expected",
                "Open Group photos and adjust the strictness slider, preview the result, then apply the groups again.",
              ],
              ["A photo looks tilted after aligning", "It probably needed more turning than allowed. Raise Settings → Alignment → Rotation clamp (e.g. 25–35°) and align again, or fix that photo in the alignment editor."],
              ["A photo has the wrong date", "Open it in the Library and use change date in the drawer; select many to shift dates in bulk. Low-confidence dates are marked."],
              ["My arms look different sizes between photos", "Turn on Pose match in the Viewer to hide photos whose arms are held differently from the reference, and Hide sleeves for arm poses."],
              ["New photos aren't grouped", "Run Process everything from the Dashboard. When automatic upload processing is enabled, new photos are added to existing groups."],
            ].map(([q, a]) => (
              <div key={q} className="px-4 py-3 text-sm">
                <div className="font-medium">{q}</div>
                <div className="text-muted">{a}</div>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Keyboard shortcuts">
          <div className="grid gap-3 text-sm md:grid-cols-3">
            <div className="card p-4">
              <div className="mb-2 font-medium">Viewer</div>
              <div className="space-y-1 text-muted">
                <div><Kbd>Space</Kbd> play / pause</div>
                <div><Kbd>←</Kbd> <Kbd>→</Kbd> step (<Kbd>Shift</Kbd> ×10)</div>
                <div><Kbd>Home</Kbd> / <Kbd>End</Kbd> first / last</div>
                <div><Kbd>[</Kbd> set comparison photo</div>
                <div><Kbd>O</Kbd> onion · <Kbd>S</Kbd> split</div>
              </div>
            </div>
            <div className="card p-4">
              <div className="mb-2 font-medium">Library</div>
              <div className="space-y-1 text-muted">
                <div>click · <Kbd>Shift</Kbd>-click · <Kbd>Ctrl</Kbd>-click select</div>
                <div><Kbd>Ctrl</Kbd>+<Kbd>A</Kbd> select all</div>
                <div><Kbd>A</Kbd> accept the open photo's pose</div>
                <div><Kbd>Esc</Kbd> close the drawer</div>
              </div>
            </div>
            <div className="card p-4">
              <div className="mb-2 font-medium">Alignment editor · Review queue</div>
              <div className="space-y-1 text-muted">
                <div><Kbd>Q</Kbd> / <Kbd>E</Kbd> turn (<Kbd>Shift</Kbd> fine)</div>
                <div><Kbd>G</Kbd> reference ghost · <Kbd>B</Kbd> unaligned</div>
                <div><Kbd>[</Kbd> / <Kbd>]</Kbd> previous / next · <Kbd>Ctrl</Kbd>+<Kbd>S</Kbd> save</div>
                <div>Review: <Kbd>A</Kbd> accept · <Kbd>R</Kbd> reject · <Kbd>U</Kbd> undo</div>
              </div>
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
