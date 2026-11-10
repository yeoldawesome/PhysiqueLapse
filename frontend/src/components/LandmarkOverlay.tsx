import { useQuery } from "@tanstack/react-query";
import { get } from "../api/client";
import type { Landmarks } from "../api/types";

// MediaPipe 33-point topology (indices into points[])
export const CONNECTIONS: [number, number][] = [
  [11, 12],
  [11, 13],
  [13, 15],
  [12, 14],
  [14, 16],
  [11, 23],
  [12, 24],
  [23, 24],
  [23, 25],
  [25, 27],
  [24, 26],
  [26, 28],
  [27, 29],
  [29, 31],
  [28, 30],
  [30, 32],
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 7],
  [0, 4],
  [4, 5],
  [5, 6],
  [6, 8],
  [9, 10],
  [15, 17],
  [15, 19],
  [15, 21],
  [16, 18],
  [16, 20],
  [16, 22],
];

export function useLandmarks(photoId: number | null, enabled = true) {
  return useQuery({
    queryKey: ["landmarks", photoId],
    queryFn: () => get<Landmarks>(`/photos/${photoId}/landmarks`),
    enabled: enabled && photoId != null,
    retry: false,
    staleTime: 60_000,
  });
}

/** Draws the skeleton in image pixel coordinates; the parent scales via viewBox. */
export function Skeleton({ lm, stroke = 1.5, color = "#6ea8fe" }: { lm: Landmarks; stroke?: number; color?: string }) {
  const pts = lm.points;
  const s = Math.max(lm.image_w, lm.image_h) / 400; // keep marker sizes readable on big images
  return (
    <g>
      {CONNECTIONS.map(([a, b], i) => {
        const pa = pts[a];
        const pb = pts[b];
        if (!pa || !pb) return null;
        const vis = Math.min(pa.visibility, pb.visibility);
        return (
          <line
            key={i}
            x1={pa.x_px}
            y1={pa.y_px}
            x2={pb.x_px}
            y2={pb.y_px}
            stroke={color}
            strokeWidth={stroke * s}
            strokeOpacity={vis >= 0.5 ? 0.9 : 0.25}
            strokeLinecap="round"
          />
        );
      })}
      {pts.map((p, i) => (
        <circle
          key={i}
          cx={p.x_px}
          cy={p.y_px}
          r={(i <= 10 ? 1.6 : 2.4) * s}
          fill={p.visibility >= 0.5 ? "#fff" : "#f87171"}
          fillOpacity={p.visibility >= 0.5 ? 0.95 : 0.6}
        >
          <title>
            {p.name} · vis {(p.visibility * 100).toFixed(0)}%
          </title>
        </circle>
      ))}
    </g>
  );
}

export default function LandmarkOverlay({ photoId, width, height, flip }: { photoId: number; width: number; height: number; flip: boolean }) {
  const { data, isError } = useLandmarks(photoId);
  if (!data || isError) return null;
  if (!data.detected) {
    return (
      <div className="pointer-events-none absolute inset-x-0 bottom-2 text-center text-xs text-danger drop-shadow">
        no person detected
      </div>
    );
  }
  // landmarks come already flipped from the API when the photo is flipped
  void flip;
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
      <Skeleton lm={data} />
    </svg>
  );
}
