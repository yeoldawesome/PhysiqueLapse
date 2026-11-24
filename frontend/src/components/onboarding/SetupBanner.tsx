import { Link, useLocation } from "react-router";
import { ArrowRight, Hand } from "lucide-react";
import { useOnboarding } from "../../api/onboarding";
import { Spinner } from "../ui";

/** Shown on every page until the first-run pose step is done. */
export default function SetupBanner() {
  const { data: st } = useOnboarding();
  const loc = useLocation();
  if (!st || loc.pathname.startsWith("/setup")) return null;
  if (st.stage === "prepare") {
    const left = st.landmarks_pending + Math.max(0, st.physique_photos - st.features_ready);
    return (
      <div className="flex items-center gap-2 border-b border-border bg-panel-2/60 px-4 py-2 text-sm">
        <Spinner size={14} />
        <span>
          Getting your photos ready for the pose step{left > 0 ? ` (${left} left)` : ""}. Nothing is sorted until you've shown the app your poses.
        </span>
        <Link to="/setup" className="btn btn-sm ml-auto">
          Open <ArrowRight size={12} />
        </Link>
      </div>
    );
  }
  if (st.stage === "poses") {
    return (
      <div className="flex items-center gap-2 border-b border-accent/40 bg-accent/10 px-4 py-2 text-sm">
        <Hand size={16} className="text-accent" />
        <span>
          <b>{st.physique_photos} photos</b> show your physique. Before they're sorted, pick a few of your own photos for each pose.
        </span>
        <Link to="/setup" className="btn btn-primary btn-sm ml-auto">
          Show your poses <ArrowRight size={12} />
        </Link>
      </div>
    );
  }
  return null;
}
