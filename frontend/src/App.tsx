import { useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation } from "react-router";
import {
  Activity,
  BookOpen,
  Camera,
  Images,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Play,
  Settings as SettingsIcon,
  Shield,
  UserCircle,
  Upload,
  UserX,
  X,
  type LucideIcon,
} from "lucide-react";
import JobDrawer from "./components/JobDrawer";
import Toasts from "./components/Toasts";
import { useDashboard } from "./api/queries";
import { useAuthStatus, useLogout } from "./api/auth";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: "/guide", label: "Start here", icon: BookOpen },
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/capture", label: "Capture", icon: Camera },
  { to: "/library", label: "Library", icon: Images },
  { to: "/import", label: "Import", icon: Upload },
  { to: "/review", label: "Review queue", icon: ListChecks },
  { to: "/people", label: "Filter people", icon: UserX },
  { to: "/viewer", label: "Viewer", icon: Play },
  { to: "/stats", label: "Stats", icon: Activity },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

/** Phone bottom bar: the pages used most on the go; everything else is under "More". */
const TABS: NavItem[] = [
  { to: "/", label: "Home", icon: LayoutDashboard, end: true },
  { to: "/library", label: "Library", icon: Images },
  { to: "/capture", label: "Capture", icon: Camera },
  { to: "/viewer", label: "Viewer", icon: Play },
];

export default function App() {
  const auth = useAuthStatus();
  if (auth.data?.auth_required && !auth.data.logged_in) {
    return <Navigate to={`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`} replace />;
  }
  return <Shell server={auth.data?.mode === "server"} />;
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

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const b = useBadges();
  const user = useAuthStatus().data?.user;
  const items: NavItem[] = [
    ...NAV,
    ...(user ? [{ to: "/account", label: "Account", icon: UserCircle }] : []),
    ...(user?.is_admin ? [{ to: "/admin", label: "Admin", icon: Shield }] : []),
  ];
  return (
    <nav className="flex flex-col gap-0.5 px-2">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-2.5 rounded-md px-2.5 py-2.5 text-sm transition-colors md:py-2 ${
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

function Brand({ server }: { server: boolean }) {
  return (
    <div className="flex items-center gap-2 px-4 py-4">
      <div className="h-7 w-7 rounded-md bg-gradient-to-br from-accent to-[#a78bfa]" />
      <div>
        <div className="text-sm font-semibold leading-tight">PhysiqueLapse</div>
        <div className="text-[11px] text-muted">{server ? "home server · private" : "local · private"}</div>
      </div>
    </div>
  );
}

function Shell({ server }: { server: boolean }) {
  const b = useBadges();
  const logout = useLogout();
  const [more, setMore] = useState(false);
  const loc = useLocation();
  useEffect(() => setMore(false), [loc.pathname]);
  const footer = (
    <div className="mt-auto flex items-center justify-between px-4 py-3 text-[11px] text-muted">
      <span>{b.photos != null ? `${b.photos} photos` : "…"}</span>
      {server && (
        <button className="flex items-center gap-1 py-1 hover:text-text" onClick={logout} title="Log out">
          <LogOut size={12} /> Log out
        </button>
      )}
    </div>
  );
  const moreActive = !TABS.some((t) => (t.end ? loc.pathname === t.to : loc.pathname.startsWith(t.to)));
  return (
    <div className="flex h-full">
      {/* desktop / tablet: sidebar */}
      <aside className="hidden w-52 shrink-0 flex-col border-r border-border bg-panel md:flex">
        <Brand server={server} />
        <NavList />
        {footer}
      </aside>
      <main className="relative min-w-0 flex-1 overflow-auto max-md:mb-[calc(3.5rem+env(safe-area-inset-bottom))]">
        <Outlet />
      </main>

      {/* phone: bottom tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {TABS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `relative flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[10px] ${isActive ? "text-accent" : "text-muted"}`
            }
          >
            {to === "/capture" ? (
              <span className="-mt-5 flex h-12 w-12 items-center justify-center rounded-full bg-accent-2 text-white shadow-lg shadow-black/40">
                <Icon size={24} />
              </span>
            ) : (
              <Icon size={20} />
            )}
            {label}
            {to === "/library" && b.needsReview > 0 && <span className="absolute right-[calc(50%-18px)] top-2 h-2 w-2 rounded-full bg-warn" />}
          </NavLink>
        ))}
        <button
          className={`relative flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[10px] ${moreActive || more ? "text-accent" : "text-muted"}`}
          onClick={() => setMore(true)}
        >
          <Menu size={20} />
          More
          {(b.review > 0 || b.notMe > 0) && <span className="absolute right-[calc(50%-18px)] top-2 h-2 w-2 rounded-full bg-accent" />}
        </button>
      </nav>
      {more && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60 md:hidden" onClick={() => setMore(false)}>
          <div
            className="flex max-h-[85vh] flex-col overflow-y-auto rounded-t-2xl border-t border-border bg-panel pb-[env(safe-area-inset-bottom)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center">
              <Brand server={server} />
              <button className="btn btn-ghost ml-auto mr-2" onClick={() => setMore(false)} aria-label="close">
                <X size={18} />
              </button>
            </div>
            <NavList onNavigate={() => setMore(false)} />
            {footer}
          </div>
        </div>
      )}
      <JobDrawer />
      <Toasts />
    </div>
  );
}
