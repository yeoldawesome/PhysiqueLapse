// Mirrors backend/app/schemas.py

export type DateSource = "exif" | "snapchat_metadata" | "filename" | "file_mtime" | "manual" | "none";
export type Confidence = "high" | "medium" | "low";
export type LandmarkStatus = "pending" | "ok" | "none" | "failed";
export type PoseSource = "rule" | "knn" | "manual" | "none";

export interface AlignmentInfo {
  photo_id: number;
  reference_photo_id: number | null;
  pose_category_id: number | null;
  canvas_w: number;
  canvas_h: number;
  scale: number;
  rotation_deg: number;
  tx: number;
  ty: number;
  method: "auto" | "manual";
  scale_basis_used: string;
  flags: Record<string, unknown>;
  residual_px: number | null;
  joint_residual_px: number | null;
  stale: boolean;
  has_output: boolean;
  computed_at: string | null;
}

export interface Photo {
  id: number;
  sha256: string;
  original_filename: string;
  ext: string;
  source: "upload" | "folder" | "snapchat";
  source_ref: string | null;
  width: number;
  height: number;
  taken_at: string | null;
  taken_at_source: DateSource;
  taken_at_confidence: Confidence;
  flip_horizontal: boolean;
  flip_source: "manual" | "auto" | "none";
  pose_category_id: number | null;
  pose_confidence: number | null;
  pose_source: PoseSource;
  pose_needs_review: boolean;
  landmarks_status: LandmarkStatus;
  landmarks_quality: number | null;
  excluded: boolean;
  abs_visible: boolean | null;
  abs_score: number | null;
  abs_source: "auto" | "manual" | "none";
  sleeves: boolean | null;
  sleeves_score: number | null;
  sleeves_source: "auto" | "manual" | "none";
  face_visible: boolean | null;
  face_score: number | null;
  face_source: "auto" | "manual" | "none";
  notes: string | null;
  created_at: string;
  updated_at: string;
  thumb_url: string;
  preview_url: string;
  original_url: string;
  aligned_url: string | null;
  alignment: AlignmentInfo | null;
  // detail only
  pose_category_name?: string | null;
  is_reference?: boolean;
  has_label?: boolean;
  original_path?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface PhotoPatch {
  taken_at?: string | null;
  clear_date?: boolean;
  flip_horizontal?: boolean;
  pose_category_id?: number | null;
  clear_pose?: boolean;
  excluded?: boolean;
  abs_visible?: boolean;
  abs_auto?: boolean;
  sleeves?: boolean;
  sleeves_auto?: boolean;
  face_visible?: boolean;
  face_auto?: boolean;
  notes?: string;
}

export interface BulkPatch {
  ids: number[];
  patch?: PhotoPatch;
  date_shift_days?: number;
  rederive_date_from?: "exif" | "filename" | "mtime" | "auto";
  toggle_flip?: boolean;
  accept_pose?: boolean;
  delete?: boolean;
}

export interface Category {
  id: number;
  name: string;
  sort_order: number;
  reference_photo_id: number | null;
  anchor_mode: "auto" | "face" | "ears";
  scale_basis: "torso" | "nose_hip" | "head";
  rotation_basis: "torso" | "eyes" | "shoulders" | "none";
  align_method: "joints" | "body" | "head";
  normalize_facing: boolean;
  framing_zoom: number;
  framing_dx: number;
  framing_dy: number;
  rule_key: string | null;
  is_builtin: boolean;
  photo_count: number;
  label_count?: number;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: number;
  type: string;
  title: string;
  lane: string;
  status: JobStatus;
  done: number;
  total: number;
  message: string;
  params: Record<string, unknown>;
  result: Record<string, unknown> & { message?: string; counts?: Record<string, number> };
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ImportSession {
  id: number;
  kind: "upload" | "folder" | "snapchat";
  root_path: string | null;
  status: string;
  counts: Record<string, number>;
  options: Record<string, unknown>;
  job_id: number | null;
  created_at: string;
  finished_at: string | null;
  decisions?: Record<string, number>;
}

export interface Candidate {
  id: number;
  session_id: number | null;
  file_path: string;
  file_name: string;
  sha256: string;
  source_date: string | null;
  date_source: string;
  media_type: string;
  clip_score: number | null;
  pose_score: number | null;
  score: number | null;
  decision: string;
  decided_by: string;
  photo_id: number | null;
  duplicate_of_photo_id: number | null;
  error: string | null;
  created_at: string;
  decided_at: string | null;
  thumb_url: string | null;
}

export interface Settings {
  canvas_w: number;
  canvas_h: number;
  rotation_clamp_deg: number;
  bg_removal: boolean;
  units: "lb" | "kg";
  stats_window_days: number;
  timezone: string;
  phash_threshold: number;
  pose_model: "lite" | "full" | "heavy";
  landmark_input_long_side: number;
  flip_tta: boolean;
  review_confidence: number;
  knn_k: number;
  knn_min_labels: number;
  knn_min_categories: number;
  filter_accept: number;
  filter_reject: number;
  filter_clip_weight: number;
  filter_pose_weight: number;
  filter_pose_gate: number;
  clip_positive_prompts: string[];
  clip_negative_prompts: string[];
  playback_fps: number;
  playback_crossfade: boolean;
  skip_near_duplicates: boolean;
}

export interface SystemInfo {
  data_dir: string;
  db_path: string;
  disk_free_gb: number;
  data_size_mb: number;
  photos: number;
  models: Record<string, { path: string; present: boolean; bytes: number; name?: string }>;
  active_pose_model: string;
  ffmpeg: string | null;
  versions: Record<string, string | null>;
  bind: string;
}

export interface Dashboard {
  counts: Record<string, number>;
  categories: Category[];
  abs: { visible: number; checked: number; unchecked: number };
  face: { visible: number; checked: number; unchecked: number };
  date_span: { first: string | null; last: string | null };
  latest: Photo[];
  weight: {
    unit: string;
    latest: number;
    latest_date: string;
    first: number;
    first_date: string;
    change: number;
    entries: number;
  } | null;
}

export interface Browse {
  path: string;
  parent: string | null;
  dirs: string[];
  images: number;
  other_files: number;
  roots: string[];
}

export interface LandmarkPoint {
  name: string;
  x_px: number;
  y_px: number;
  z: number;
  visibility: number;
  presence: number;
}

export interface Landmarks {
  model: string;
  image_w: number;
  image_h: number;
  detected: boolean;
  points: LandmarkPoint[];
  flip_horizontal?: boolean;
}

export interface SequenceFrame {
  id: number;
  taken_at: string | null;
  days_since_start: number | null;
  aligned_url: string | null;
  preview_url: string;
  flags: Record<string, unknown>;
  method: string;
  stale: boolean;
  flip_horizontal: boolean;
  stats: StatAt[];
  measurements: Record<string, number>;
  pose_diff_pct: number | null;
  sleeves?: boolean | null; // clothing covers the arms (null = not checked)
}

export interface Sequence {
  pose_category_id: number;
  canvas_w: number;
  canvas_h: number;
  reference_photo_id: number | null;
  frames: SequenceFrame[];
  unaligned: number;
  without_date: number;
}

export interface Metric {
  id: number;
  name: string;
  unit: string;
  is_builtin: boolean;
  sort_order: number;
  decimals: number;
  kind: string;
  entry_count?: number;
  latest?: { date: string; value: number } | null;
}

export interface MetricEntry {
  id: number;
  metric_id: number;
  date: string;
  value: number;
  note: string | null;
}

export interface StatAt {
  metric_id: number;
  name: string;
  unit: string;
  decimals: number;
  value: number | null;
  date: string | null;
  offset_days: number | null;
  offset_label: string | null;
  change_since_first: number | null;
  first_value: number | null;
}

export interface ExportItem {
  id: number;
  pose_category_id: number | null;
  format: string;
  path: string | null;
  params: Record<string, unknown>;
  frames: number;
  size_bytes: number;
  status: string;
  job_id: number | null;
  created_at: string;
  download_url: string;
}

export interface SnapScan {
  path: string;
  memories_dir: string | null;
  metadata_file: string | null;
  metadata_entries: number;
  images: number;
  videos: number;
  overlays: number;
  other: number;
  matched_metadata: number;
  sample_files: string[];
  already_known: number;
  warnings: string[];
}
