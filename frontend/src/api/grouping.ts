import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, post } from "./client";
import type { Job } from "./types";

export interface GroupPreview {
  name: string;
  size: number;
  medoid_photo_id: number;
  spread: number;
  thumb_url: string;
  sample_urls: string[];
}

export interface GroupingPreview {
  threshold: number;
  min_size: number;
  photos: number;
  groups: GroupPreview[];
  ungrouped: number;
  without_body: number;
}

export interface GroupingStatus {
  active: boolean;
  threshold: number;
  min_size: number;
}

export const STRICT_MIN = 0.04;
export const STRICT_MAX = 0.4;
export const toThreshold = (strict: number) => +(STRICT_MAX - (strict / 100) * (STRICT_MAX - STRICT_MIN)).toFixed(3);
export const toStrictness = (threshold: number) => Math.max(0, Math.min(100, Math.round((100 * (STRICT_MAX - threshold)) / (STRICT_MAX - STRICT_MIN))));

export const useGroupingStatus = () => useQuery({ queryKey: ["grouping"], queryFn: () => get<GroupingStatus>("/grouping") });

export const useGroupingPreview = (threshold: number, minSize: number) =>
  useQuery({
    queryKey: ["grouping", "preview", threshold, minSize],
    queryFn: () => get<GroupingPreview>("/grouping/preview", { threshold, min_size: minSize }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

export const applyGrouping = (threshold: number, minSize: number) =>
  post<{ job: Job }>("/grouping/apply", { threshold, min_size: minSize });

export const mergeGroup = (cid: number, into: number) => post<{ moved: number; job: Job }>(`/grouping/merge/${cid}`, { into });
