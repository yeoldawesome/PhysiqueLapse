/**
 * Live pose matching on the Capture page: MediaPipe Pose (lite) in the browser, on the camera
 * preview. The model comes from this app (/api/capture/pose-model) and the WebAssembly from
 * /mediapipe; nothing is fetched from Google. Loaded only when matching is switched on.
 */
import type { PoseLandmarker } from "@mediapipe/tasks-vision";
import type { GhostPoint } from "../../api/capture";
import { cropToAspect } from "./camera";

// MediaPipe Pose landmark order (same as the server's LANDMARK_NAMES)
export const NAMES = [
  "nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
  "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder", "right_shoulder", "left_elbow",
  "right_elbow", "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index", "right_index",
  "left_thumb", "right_thumb", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
  "left_heel", "right_heel", "left_foot_index", "right_foot_index",
];

/** The joints that define the poses: shoulders, elbows, wrists, hips. */
export const KEY_JOINTS = ["left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip"];

let loading: Promise<PoseLandmarker> | null = null;

export function loadLivePose(): Promise<PoseLandmarker> {
  loading ??= (async () => {
    const { FilesetResolver, PoseLandmarker } = await import("@mediapipe/tasks-vision");
    const files = await FilesetResolver.forVisionTasks("/mediapipe");
    const make = (delegate: "GPU" | "CPU") =>
      PoseLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: "/api/capture/pose-model", delegate },
        // IMAGE mode (a fresh detection each frame), like the server. VIDEO mode's tracking
        // drifts on non-square camera frames (its landmark projection assumes a square ROI).
        runningMode: "IMAGE",
        numPoses: 1,
        minPoseDetectionConfidence: 0.5,
        minPosePresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
    try {
      return await make("GPU");
    } catch {
      return await make("CPU"); // no WebGL2: slower, still fine at a few frames a second
    }
  })();
  loading.catch(() => {
    loading = null; // let a later attempt retry (e.g. the model was still downloading)
  });
  return loading;
}

/** Landmarks of the current video frame, x/y as 0..1 of the video frame, or null. */
export function detect(lm: PoseLandmarker, video: HTMLVideoElement): GhostPoint[] | null {
  const r = lm.detect(video);
  const pose = r.landmarks?.[0];
  if (!pose) return null;
  return pose.map((p, i) => ({ name: NAMES[i], x: p.x, y: p.y, v: p.visibility ?? 1 }));
}

/** Ghost landmarks moved into the video frame (the same centre crop the ghost image gets). */
export function ghostInVideo(ghost: GhostPoint[], gw: number, gh: number, vw: number, vh: number): Map<string, GhostPoint> {
  const c = cropToAspect(gw, gh, vw / vh);
  return new Map(ghost.map((p) => [p.name, { ...p, x: (p.x * gw - c.sx) / c.sw, y: (p.y * gh - c.sy) / c.sh }]));
}

export interface Match {
  score: number; // 0..100
  joints: Map<string, number>; // distance per key joint, in torso lengths
}

const dist = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by);

// The browser runs the lite model, the ghost's landmarks come from the heavy one: on the very same
// photo they still differ by ~0.05 torso lengths. That much counts as "on top".
const NOISE = 0.04;
const RANGE = 0.3; // average distance (beyond the noise) at which the score reaches 0
const EDGE = 0.02; // joints this close to the frame edge are guesses (body cut off): not compared

const inFrame = (p: GhostPoint) => p.x > EDGE && p.x < 1 - EDGE && p.y > EDGE && p.y < 1 - EDGE;

/**
 * How well the live body sits on the ghost, where it is in the frame. No fitting: lining up means
 * the same distance and position too. Each key joint's distance is measured in the ghost's torso
 * lengths (shoulder midpoint to hip midpoint); 100 = on top, 0 = ~a third of a torso off on average.
 */
export function matchPose(live: GhostPoint[], ghost: Map<string, GhostPoint>, vw: number, vh: number): Match | null {
  const g = (n: string) => ghost.get(n);
  const ls = g("left_shoulder"), rs = g("right_shoulder"), lh = g("left_hip"), rh = g("right_hip");
  if (!ls || !rs || !lh || !rh) return null;
  const torso = dist(((ls.x + rs.x) / 2) * vw, ((ls.y + rs.y) / 2) * vh, ((lh.x + rh.x) / 2) * vw, ((lh.y + rh.y) / 2) * vh);
  if (torso < 10) return null;
  const by = new Map(live.map((p) => [p.name, p]));
  const joints = new Map<string, number>();
  for (const n of KEY_JOINTS) {
    const a = by.get(n), b = g(n);
    if (!a || !b || a.v < 0.5 || b.v < 0.5 || !inFrame(a) || !inFrame(b)) continue;
    joints.set(n, dist(a.x * vw, a.y * vh, b.x * vw, b.y * vh) / torso);
  }
  if (joints.size < 3) return null;
  const mean = [...joints.values()].reduce((s, d) => s + Math.max(0, d - NOISE), 0) / joints.size;
  return { score: Math.round(100 * Math.max(0, Math.min(1, 1 - mean / RANGE))), joints };
}

/** Joint colour: green when close, amber when near, red when off; grey when not compared. */
export function jointColor(d: number | undefined): string {
  if (d == null) return "rgba(255,255,255,0.6)";
  return d < NOISE + 0.06 ? "#34d399" : d < NOISE + 0.16 ? "#fbbf24" : "#f87171";
}
