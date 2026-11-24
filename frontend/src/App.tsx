import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import {
  Activity,
  BookOpen,
  Images,
  LayoutDashboard,
  ListChecks,
  Play,
  Settings as SettingsIcon,
  Upload,
  UserX,
  type LucideIcon,
} from "lucide-react";
import JobDrawer from "./components/JobDrawer";
import Toasts from "./components/Toasts";
import { useDashboard } from "./api/queries";
import { useOnboarding } from "./api/onboarding";
import Tutorial from "./components/onboarding/Tutorial";
import SetupBanner from "./components/onboarding/SetupBanner";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/guide", label: "Start here", icon: BookOpen },
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/library", label: "Library", icon: Images },
  { to: "/import", label: "Import", icon: Upload },
  { to: "/review", label: "Review queue", icon: ListChecks },
  { to: "/people", label: "Filter people", icon: UserX },
  { to: "/viewer", label: "Viewer", icon: Play },
  { to: "/stats", label: "Stats", icon: Activity },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  return <Shell />;
}

function useBadges() {
  const dash = useDashboard();
  return {
    photos: dash.data?.counts.photos,
    review: dash.data?.counts.review_queue ?? 0,
    needsReview: dash.data?.counts.needs_review ?? 0,
    notMe: dash.data?.people?.not_me ?? 0,
  };
}

function Badge({ to, b }: { to: string; b: ReturnType<typeof useBadges> }) {
  if (to === "/review" && b.review > 0) return <span className="badge bg-accent-2/20 text-accent">{b.review}</span>;
  if (to === "/people" && b.notMe > 0)
    return (
      <span className="badge bg-danger/15 text-danger" title="photos that don't look like you">
        {b.notMe}
      </span>
    );
  if (to === "/library" && b.needsReview > 0)
    return (
      <span className="badge bg-warn/15 text-warn" title="poses needing review">
        {b.needsReview}
      </span>
    );
  return null;
}

function NavList() {
  const b = useBadges();
  return (
    <nav className="flex flex-col gap-0.5 px-2">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            `flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors ${
              isActive ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2/60 hover:text-text"
            }`
          }
        >
          <Icon size={16} />
          <span className="flex-1">{label}</span>
          <Badge to={to} b={b} />
        </NavLink>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-4 py-4">
      <div className="h-7 w-7 rounded-md bg-gradient-to-br from-accent to-[#a78bfa]" />
      <div>
        <div className="text-sm font-semibold leading-tight">PhysiqueLapse</div>
        <div className="text-[11px] text-muted">local · private</div>
      </div>
    </div>
  );
}

function Shell() {
  const b = useBadges();
  const { data: onboarding } = useOnboarding();
  const [tutorialClosed, setTutorialClosed] = useState(false);
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-panel">
        <Brand />
        <NavList />
        <div className="mt-auto px-4 py-3 text-[11px] text-muted">{b.photos != null ? `${b.photos} photos` : "…"}</div>
      </aside>
      <main className="relative min-w-0 flex-1 overflow-auto">
        <SetupBanner />
        <Outlet />
      </main>
      <Tutorial open={!!onboarding?.show_tutorial && !tutorialClosed} onClose={() => setTutorialClosed(true)} />
      <JobDrawer />
      <Toasts />
    </div>
  );
}
