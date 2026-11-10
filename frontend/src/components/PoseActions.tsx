import { Link } from "react-router";
import { Crosshair, Play } from "lucide-react";
import { useRunJob } from "../api/queries";

/** View + Align for a pose, used on the Dashboard cards and at the top of the Library.
 * Abs and Face are close-up groups: aligned automatically when found, so only View. */
export default function PoseActions({
  poseId,
  photoCount,
  hasReference,
  size = "sm",
  stretch = false,
}: {
  poseId: number | "abs" | "face";
  photoCount: number;
  hasReference?: boolean;
  size?: "sm" | "md";
  stretch?: boolean;
}) {
  const run = useRunJob();
  const btn = `btn ${size === "sm" ? "btn-sm" : ""} ${stretch ? "flex-1 justify-center" : ""}`;
  const icon = size === "sm" ? 12 : 14;
  const closeup = typeof poseId !== "number";
  return (
    <div className={`flex gap-1.5 ${stretch ? "w-full" : ""}`}>
      {photoCount > 0 ? (
        <Link to={`/viewer?pose_id=${poseId}`} className={btn} title="play this pose as a time-lapse">
          <Play size={icon} /> View
        </Link>
      ) : (
        <button className={btn} disabled title="no photos in this pose yet">
          <Play size={icon} /> View
        </button>
      )}
      {!closeup && (
        <button
          className={btn}
          disabled={photoCount === 0 || !hasReference || run.isPending}
          title={!hasReference ? "pick a reference first: star a photo of this pose in the Library" : "line up every photo of this pose with its reference"}
          onClick={() => run.mutate({ path: `/poses/${poseId}/align` })}
        >
          <Crosshair size={icon} /> Align
        </button>
      )}
    </div>
  );
}
