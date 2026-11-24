import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, patch, post } from "./client";
import type { Job } from "./types";
import { toast } from "../lib/toast";

export type OnboardingStage = "import" | "prepare" | "group" | "done";

export interface OnboardingStatus {
  stage: OnboardingStage;
  show_tutorial: boolean;
  tutorial_seen: boolean;
  pose_setup_done: boolean;
  skipped: number[];
  photos: number;
  landmarks_pending: number;
  physique_photos: number;
  features_ready: number;
}

export interface SetupPose {
  id: number;
  name: string;
  rule_key: string | null;
  mirror_id: number | null;
  examples: number[];
  uploaded: number;
}

export interface PoseCandidate {
  photo_id: number;
  score: number;
  picked: boolean;
  taken_at: string | null;
}

const KEY = ["onboarding"] as const;

/** Polls while photos are being prepared, so the next step appears by itself. */
export function useOnboarding() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => get<OnboardingStatus>("/onboarding"),
    refetchInterval: (q) => (q.state.data?.stage === "prepare" ? 1500 : 10_000),
  });
}

export function useSetupPoses(enabled: boolean) {
  return useQuery({ queryKey: [...KEY, "poses"], queryFn: () => get<SetupPose[]>("/onboarding/poses"), enabled });
}

export function usePoseCandidates(poseId: number | undefined, limit: number) {
  return useQuery({
    queryKey: [...KEY, "candidates", poseId, limit],
    queryFn: () => get<PoseCandidate[]>(`/onboarding/poses/${poseId}/candidates`, { limit }),
    enabled: poseId != null,
  });
}

export function useOnboardingMutations() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const onError = (e: Error) => toast.error(e.message);
  return {
    seenTutorial: useMutation({ mutationFn: () => patch<OnboardingStatus>("/onboarding", { tutorial_seen: true }), onSuccess: refresh, onError }),
    prepare: useMutation({
      mutationFn: () => post<{ job: Job }>("/onboarding/prepare"),
      onSuccess: () => {
        refresh();
        qc.invalidateQueries({ queryKey: ["jobs"] });
      },
      onError,
    }),
    /** pick (poseId) or un-pick (null) one of the user's photos as an example of a pose */
    setExample: useMutation({
      mutationFn: ({ photoId, poseId }: { photoId: number; poseId: number | null }) =>
        patch(`/photos/${photoId}`, poseId == null ? { clear_pose: true } : { pose_category_id: poseId }),
      onSuccess: () => {
        refresh();
        qc.invalidateQueries({ queryKey: ["categories"] });
      },
      onError,
    }),
    finish: useMutation({
      mutationFn: (skipped: number[]) => post<{ job: Job; status: OnboardingStatus }>("/onboarding/finish", { skipped }),
      onSuccess: () => {
        qc.invalidateQueries();
      },
      onError,
    }),
  };
}
