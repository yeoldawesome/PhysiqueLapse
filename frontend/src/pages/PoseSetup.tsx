import { useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router";
import { useOnboarding, useOnboardingMutations } from "../api/onboarding";
import GroupPhotos from "../components/GroupPhotos";
import { PageHeader, Spinner } from "../components/ui";

export default function PoseSetup() {
  const { data: status } = useOnboarding();
  const { prepare } = useOnboardingMutations();
  const navigate = useNavigate();
  const started = useRef(false);
  useEffect(() => {
    if (status?.stage === "prepare" && !started.current) {
      started.current = true;
      prepare.mutate();
    }
  }, [status?.stage, prepare]);
  return (
    <div>
      <PageHeader title="Group photos automatically" subtitle="No pose examples or labels are needed." />
      <div className="mx-auto flex max-w-6xl flex-col gap-4 p-3 md:p-6">
        {!status ? (
          <Spinner />
        ) : status.stage === "import" ? (
          <div className="card flex flex-col items-start gap-3 p-6">
            <h2 className="text-lg font-semibold">Import photos first</h2>
            <p className="text-sm text-muted">After import, the app detects poses and groups similar photos automatically.</p>
            <Link to="/import" className="btn btn-primary">
              Import photos
            </Link>
          </div>
        ) : status.stage === "prepare" ? (
          <div className="card flex items-center gap-3 p-6">
            <Spinner />
            <div>
              <h2 className="font-semibold">Finding body landmarks</h2>
              <p className="text-sm text-muted">
                {status.photos - status.landmarks_pending} of {status.photos} photos processed. Groups will be ready when this finishes.
              </p>
            </div>
          </div>
        ) : (
          <GroupPhotos onApplied={() => navigate("/")} />
        )}
      </div>
    </div>
  );
}
