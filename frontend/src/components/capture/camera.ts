/** Camera helpers for the Capture page: stream, ghost framing, still capture. */
import type { GhostPoint } from "../../api/capture";
import { withExif } from "../../lib/exif";

export async function openCamera(facing: "user" | "environment"): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(window.isSecureContext ? "This browser has no camera access." : "The camera needs the https:// address of the app.");
  }
  // Ask for the biggest frame the camera can give; the browser picks the closest it supports.
  return navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: { ideal: facing }, width: { ideal: 4096 }, height: { ideal: 4096 } },
  });
}

export function stopStream(s: MediaStream | null) {
  s?.getTracks().forEach((t) => t.stop());
}

/** The part of the ghost image that shows the same field of view as a video frame of aspect
 * `aspect` (w/h): a centred crop. Both are then drawn with the same object-fit:cover, so a
 * pixel of the ghost lands on the matching pixel of the live picture. */
export function cropToAspect(gw: number, gh: number, aspect: number) {
  if (gw / gh > aspect) {
    const w = gh * aspect;
    return { sx: (gw - w) / 2, sy: 0, sw: w, sh: gh };
  }
  const h = gw / aspect;
  return { sx: 0, sy: (gh - h) / 2, sw: gw, sh: h };
}

const BONES: [string, string][] = [
  ["left_shoulder", "right_shoulder"],
  ["left_shoulder", "left_elbow"],
  ["left_elbow", "left_wrist"],
  ["right_shoulder", "right_elbow"],
  ["right_elbow", "right_wrist"],
  ["left_shoulder", "left_hip"],
  ["right_shoulder", "right_hip"],
  ["left_hip", "right_hip"],
  ["left_hip", "left_knee"],
  ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"],
  ["right_knee", "right_ankle"],
];
const HEAD = ["nose", "left_eye", "right_eye", "left_ear", "right_ear"];

/** Pose skeleton in canvas coordinates. `colorFor` lets the live matcher colour joints. */
export function drawSkeleton(
  ctx: CanvasRenderingContext2D,
  pts: GhostPoint[],
  map: (p: GhostPoint) => [number, number],
  scale: number,
  colorFor: (name: string) => string = () => "#ffffff",
) {
  const by = new Map(pts.map((p) => [p.name, p]));
  ctx.lineCap = "round";
  ctx.lineWidth = Math.max(3, 6 * scale);
  for (const [a, b] of BONES) {
    const pa = by.get(a);
    const pb = by.get(b);
    if (!pa || !pb || pa.v < 0.3 || pb.v < 0.3) continue;
    const [x1, y1] = map(pa);
    const [x2, y2] = map(pb);
    const g = ctx.createLinearGradient(x1, y1, x2, y2);
    g.addColorStop(0, colorFor(a));
    g.addColorStop(1, colorFor(b));
    ctx.strokeStyle = g;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }
  for (const p of pts) {
    if (p.v < 0.3 || (HEAD.includes(p.name) && p.name !== "nose")) continue;
    const [x, y] = map(p);
    ctx.fillStyle = colorFor(p.name);
    ctx.beginPath();
    ctx.arc(x, y, Math.max(4, 9 * scale), 0, Math.PI * 2);
    ctx.fill();
  }
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

/** "2025-06-07T08:09:10-05:00": local time with its UTC offset. */
export function isoWithOffset(d: Date): string {
  const m = -d.getTimezoneOffset();
  const sign = m >= 0 ? "+" : "-";
  const a = Math.abs(m);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${sign}${pad(Math.floor(a / 60))}:${pad(a % 60)}`
  );
}

export function captureFilename(label: string, d: Date): string {
  const safe = label.replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "photo";
  return `PhysiqueLapse_${safe}_${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.jpg`;
}

interface ImageCaptureLike {
  takePhoto(): Promise<Blob>;
}

/**
 * A still at the best resolution available: ImageCapture.takePhoto() where the browser has it
 * (Android Chrome: full sensor resolution), otherwise the current video frame at the stream's
 * full resolution (iOS Safari). The plain photo, never the ghost; not mirrored. EXIF date added.
 */
export async function takeStill(video: HTMLVideoElement, track: MediaStreamTrack | undefined, when: Date, model: string): Promise<Blob> {
  let jpeg: Blob | null = null;
  const IC = (window as unknown as { ImageCapture?: new (t: MediaStreamTrack) => ImageCaptureLike }).ImageCapture;
  if (IC && track) {
    try {
      const b = await new IC(track).takePhoto();
      if (b.type === "image/jpeg") jpeg = b;
    } catch {
      /* fall back to the video frame */
    }
  }
  if (!jpeg) {
    const c = document.createElement("canvas");
    c.width = video.videoWidth;
    c.height = video.videoHeight;
    c.getContext("2d")!.drawImage(video, 0, 0, c.width, c.height);
    jpeg = await new Promise<Blob>((resolve, reject) => c.toBlob((b) => (b ? resolve(b) : reject(new Error("could not encode the photo"))), "image/jpeg", 0.92));
  }
  const bytes = withExif(new Uint8Array(await jpeg.arrayBuffer()), when, model);
  return new Blob([bytes as BlobPart], { type: "image/jpeg" });
}

/** Save to the phone's photos: the share sheet ("Save Image" / "Save to Photos") where
 * files can be shared, else a normal download. MUST be called straight from a tap handler
 * (before any await), or the browser refuses the share sheet. Resolves when the sheet closes. */
export function saveToCameraRoll(files: File[]): Promise<"shared" | "dismissed" | "downloaded"> {
  const nav = navigator as Navigator & { canShare?: (d: ShareData) => boolean };
  if (nav.share && nav.canShare?.({ files })) {
    return nav.share({ files }).then(
      () => "shared" as const,
      () => "dismissed" as const,
    );
  }
  for (const f of files) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(f);
    a.download = f.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
  }
  return Promise.resolve("downloaded");
}

const SAVE_KEY = "pl.capture.saveToRoll";
export function getSaveToRoll(): boolean {
  try {
    return localStorage.getItem(SAVE_KEY) !== "0";
  } catch {
    return true;
  }
}
export function setSaveToRoll(v: boolean) {
  try {
    localStorage.setItem(SAVE_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
}
