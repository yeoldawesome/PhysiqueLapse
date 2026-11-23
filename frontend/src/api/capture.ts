import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, patch, post } from "./client";

export type GhostSource = "reference" | "last" | "first";
export type GhostStyle = "photo" | "edges" | "skeleton";

export interface CaptureSeries {
  id: number;
  name: string;
  mode: "reference" | "daily";
  pose_category_id: number | null;
  reference_photo_id: number | null;
  ghost_source: GhostSource;
  ghost_style: GhostStyle;
  ghost_opacity: number;
  mirror: boolean;
  camera_facing: "user" | "environment";
  timer_s: 0 | 3 | 5 | 10;
  auto_capture: boolean;
  archived: boolean;
  photo_count: number;
  streak?: { taken_today: boolean; streak: number; days: { date: string; photo_id: number | null; thumb_url: string | null }[] };
  last_taken_at: string | null;
  last_thumb_url: string | null;
}

export interface GhostPoint {
  name: string;
  x: number; // 0..1 of the image width
  y: number;
  v: number; // visibility
}

export interface Ghost {
  photo_id: number;
  taken_at: string | null;
  width: number;
  height: number;
  image_url: string;
  edges_url: string;
  landmarks: GhostPoint[] | null;
}

export interface CaptureListing {
  poses: { pose_id: number; name: string; series_id: number | null; reference_thumb_url: string | null }[];
  daily: CaptureSeries[];
}

export const useCaptureListing = () => useQuery({ queryKey: ["capture", "series"], queryFn: () => get<CaptureListing>("/capture/series") });

export const useSeries = (id: number | null) =>
  useQuery({ queryKey: ["capture", "series", id], queryFn: () => get<CaptureSeries>(`/capture/series/${id}`), enabled: id != null });

export const useGhost = (id: number | null, source: GhostSource | undefined) =>
  useQuery({
    queryKey: ["capture", "ghost", id, source],
    queryFn: () => get<{ ghost: Ghost | null }>(`/capture/series/${id}/ghost`, { source }),
    enabled: id != null && source != null,
  });

export function usePatchSeries(id: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<CaptureSeries>) => patch<CaptureSeries>(`/capture/series/${id}`, body),
    onSuccess: (row) => {
      qc.setQueryData(["capture", "series", id], row);
      qc.invalidateQueries({ queryKey: ["capture", "series"], exact: true });
    },
  });
}

export const openPoseSeries = (poseId: number) => post<CaptureSeries>(`/capture/series/for-pose/${poseId}`);
export const createDailySeries = (name: string, poseId: number | null) =>
  post<CaptureSeries>("/capture/series", { name, pose_category_id: poseId });
