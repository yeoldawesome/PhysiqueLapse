import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { del, get, patch, post, put } from "./client";
import type {
  Browse,
  BulkPatch,
  Candidate,
  Category,
  Dashboard,
  ImportSession,
  Job,
  Page,
  Photo,
  PhotoPatch,
  Settings,
  SystemInfo,
} from "./types";
import { toast } from "../lib/toast";

export type PhotoFilters = {
  pose_id?: string;
  from?: string;
  to?: string;
  source?: string;
  date_source?: string;
  needs_review?: boolean;
  flagged?: boolean;
  no_date?: boolean;
  excluded?: "all" | "only" | "hide";
  landmarks?: string;
  q?: string;
  sort?: string;
  page?: number;
  page_size?: number;
};

export const keys = {
  photos: ["photos"] as const,
  photo: (id: number) => ["photo", id] as const,
  categories: ["categories"] as const,
  jobs: ["jobs"] as const,
  settings: ["settings"] as const,
  system: ["system"] as const,
  dashboard: ["dashboard"] as const,
  imports: ["imports"] as const,
};

export function usePhotos(filters: PhotoFilters) {
  return useQuery({
    queryKey: [...keys.photos, filters],
    queryFn: () => get<Page<Photo>>("/photos", filters as Record<string, string | number | boolean | undefined>),
    placeholderData: keepPreviousData,
  });
}

export function usePhoto(id: number | null) {
  return useQuery({
    queryKey: keys.photo(id ?? -1),
    queryFn: () => get<Photo>(`/photos/${id}`),
    enabled: id != null,
  });
}

export function useCategories() {
  return useQuery({ queryKey: keys.categories, queryFn: () => get<Category[]>("/poses"), staleTime: 30_000 });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => get<Settings>("/settings"), staleTime: 60_000 });
}

export function useSystem() {
  return useQuery({ queryKey: keys.system, queryFn: () => get<SystemInfo>("/system"), staleTime: 30_000 });
}

export function useDashboard() {
  return useQuery({ queryKey: keys.dashboard, queryFn: () => get<Dashboard>("/dashboard") });
}

export function useImports() {
  return useQuery({ queryKey: keys.imports, queryFn: () => get<ImportSession[]>("/imports") });
}

export function useImportDetail(id: number | null) {
  return useQuery({
    queryKey: [...keys.imports, id],
    queryFn: () => get<ImportSession>(`/imports/${id}`),
    enabled: id != null,
  });
}

export function useCandidates(sessionId: number | null, decision?: string) {
  return useQuery({
    queryKey: [...keys.imports, sessionId, "candidates", decision],
    queryFn: () => get<Page<Candidate>>(`/imports/${sessionId}/candidates`, { decision, page_size: 500 }),
    enabled: sessionId != null,
  });
}

/** Polls jobs every second while any are active. JobDrawer handles completion toasts and refreshes. */
export function useJobs() {
  return useQuery({
    queryKey: keys.jobs,
    queryFn: () => get<Job[]>("/jobs", { limit: 30 }),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.some((j) => j.status === "queued" || j.status === "running") ? 1000 : 5000;
    },
  });
}

export function useInvalidate() {
  const qc = useQueryClient();
  return (...which: (keyof typeof keys)[]) => {
    for (const w of which) {
      const k = keys[w];
      qc.invalidateQueries({ queryKey: typeof k === "function" ? ["photo"] : k });
    }
  };
}

export function usePatchPhoto() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: PhotoPatch }) => patch<Photo>(`/photos/${id}`, body),
    onSuccess: (p) => {
      qc.setQueryData(keys.photo(p.id), (old: Photo | undefined) => (old ? { ...old, ...p } : p));
      qc.invalidateQueries({ queryKey: keys.photos });
      qc.invalidateQueries({ queryKey: keys.categories });
      qc.invalidateQueries({ queryKey: keys.dashboard });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useBulk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkPatch) => post<{ updated: number; skipped: number }>("/photos/bulk", body),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: keys.photos });
      qc.invalidateQueries({ queryKey: ["photo"] });
      qc.invalidateQueries({ queryKey: keys.categories });
      qc.invalidateQueries({ queryKey: keys.dashboard });
      if (r.skipped) toast.info(`${r.updated} updated, ${r.skipped} skipped (no date)`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useDeletePhoto() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => del(`/photos/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.photos });
      qc.invalidateQueries({ queryKey: keys.dashboard });
      qc.invalidateQueries({ queryKey: keys.categories });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Settings>) => put<Settings>("/settings", body),
    onSuccess: (s) => {
      qc.setQueryData(keys.settings, s);
      toast.success("Settings saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useCategoryMutations() {
  const qc = useQueryClient();
  const inv = () => {
    qc.invalidateQueries({ queryKey: keys.categories });
    qc.invalidateQueries({ queryKey: keys.photos });
    qc.invalidateQueries({ queryKey: keys.dashboard });
    qc.invalidateQueries({ queryKey: ["photo"] });
  };
  const create = useMutation({
    mutationFn: (body: Partial<Category>) => post<Category>("/poses", body),
    onSuccess: inv,
    onError: (e: Error) => toast.error(e.message),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<Category> }) => patch<Category>(`/poses/${id}`, body),
    onSuccess: inv,
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/poses/${id}`),
    onSuccess: inv,
    onError: (e: Error) => toast.error(e.message),
  });
  const setReference = useMutation({
    mutationFn: ({ id, photo_id }: { id: number; photo_id: number | null }) => post<Category>(`/poses/${id}/reference`, { photo_id }),
    onSuccess: () => {
      inv();
      toast.success("Reference updated");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return { create, update, remove, setReference };
}

export function useRunJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post<Job>(path, body ?? {}),
    onSuccess: (j) => {
      qc.invalidateQueries({ queryKey: keys.jobs });
      toast.info(`${j.title} queued`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export async function browse(path: string): Promise<Browse> {
  return post<Browse>("/imports/browse", { path });
}
