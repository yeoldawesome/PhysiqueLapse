import { NavLink, Outlet } from "react-router";
import { Activity, Images, LayoutDashboard, ListChecks, Play, Settings as SettingsIcon, Upload } from "lucide-react";
import JobDrawer from "./components/JobDrawer";
import Toasts from "./components/Toasts";
import { useDashboard } from "./api/queries";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/library", label: "Library", icon: Images },
  { to: "/import", label: "Import", icon: Upload },
  { to: "/review", label: "Review queue", icon: ListChecks },
  { to: "/viewer", label: "Viewer", icon: Play },
  { to: "/stats", label: "Stats", icon: Activity },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  const dash = useDashboard();
  const review = dash.data?.counts.review_queue ?? 0;
  const needsReview = dash.data?.counts.needs_review ?? 0;
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-panel">
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="h-7 w-7 rounded-md bg-gradient-to-br from-accent to-[#a78bfa]" />
          <div>
            <div className="text-sm font-semibold leading-tight">PhysiqueLapse</div>
            <div className="text-[11px] text-muted">local · private</div>
          </div>
        </div>
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
              {to === "/review" && review > 0 && <span className="badge bg-accent-2/20 text-accent">{review}</span>}
              {to === "/library" && needsReview > 0 && (
                <span className="badge bg-warn/15 text-warn" title="poses needing review">
                  {needsReview}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-4 py-3 text-[11px] text-muted">
          {dash.data ? `${dash.data.counts.photos} photos` : "…"}
        </div>
      </aside>
      <main className="relative min-w-0 flex-1 overflow-auto">
        <Outlet />
      </main>
      <JobDrawer />
      <Toasts />
    </div>
  );
}
