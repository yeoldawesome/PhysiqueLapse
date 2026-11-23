# PhysiqueLapse — Plan

Status: **v1.9, 2026-11-09 — Abs group added (see 'Abs group'); — joint-level alignment, pose-match filter, left/right groups (see 'Joints and sides'); — body-size estimates from photos added (see 'Body sizes from photos'); — Snapchat zip upload + filter added (see 'Snapchat zips'); — finer categories + body-fit alignment (see 'Second pass' below); — M1–M6 built; M2/M3 verified on the user's real photos (122 iCloud lifting photos); M7 polish partly done.** Remaining: Snapchat parser on a real export (Q3), browser click-through, threshold calibration.

### Progress tracker (updated as work lands)

| Milestone | State | Notes |
|---|---|---|
| M1 Skeleton | **done** | Backend core, ingest/dates/dedupe, Library/Import/Dashboard/Settings UI, `start.ps1` / `start.sh` / `Makefile`. Verified: 79 pytest tests pass; frontend type-checks and builds; live server on a scratch data dir imported the 23-image synthetic set from `scripts/make_sample_set.py` (EXIF, every filename pattern, mtime, PNG/WEBP/HEIC, EXIF rotation, exact + near duplicate, video/text skipped) and bulk date shift worked. Not yet done: clicking through the UI in a browser (only API + build verified). |
| M2 Pose | **done (synthetic)** | `pose_backend` (MediaPipe 0.10.21, IMAGE mode, optional flip-TTA), landmark job + cache + flip-on-read, 16-feature vector, rules for the 5 starter poses, kNN after N labels, classify job (never overwrites manual), landmark overlay in the drawer. Verified with hand-built landmark fixtures and a fake backend; the real heavy model loads and runs (smoke test, 0.12 s/image on CPU). **Not verified on real people yet** (needs Q4). |
| M3 Alignment | **done (synthetic)** | `align.py` (similarity solve, clamp, head fallback, two-point manual solve, normalize-facing, render with INTER_AREA pre-shrink), align job (auto-picks earliest good photo as reference), editor context / manual / reset API, `/poses/{id}/eval`, `scripts/eval_alignment.py`, Alignment editor page (ghost, drag/wheel/rotate, draggable head + hip anchors, before/after, prev/next). Synthetic transforms recovered to < 1e-6 T. **Alignment quality on real photos unverified** (needs Q4). |
| M4 Viewer | **done (synthetic)** | Viewer page (single / onion / split with draggable divider, scrubber, play 0.5–10 fps with crossfade, keyboard map, outward preloading, flags overlay, stats strip, metric chart with synced marker, click chart to jump), Export dialog + exports list, `export_video` job (MP4 via bundled ffmpeg, GIF via Pillow, exact frame timing, crossfade, caption with date / day N / chosen metrics). Verified: 30-frame MP4 and GIF exports in tests (duration checked with ffmpeg), caption inspected visually on an extracted frame. |
| M5 Stats | **done** | Metrics CRUD + presets (body fat, waist, arm, chest; cm when units = kg), quick-add, inline-edit table, CSV import long + wide (comma / semicolon / tab, BOM, `Weight (lb)` style headers, bad rows reported with line numbers) with dry-run preview, sample CSVs, lb↔kg conversion of stored weight when the setting changes, nearest-date matching (exact, earlier/later, window, tie → earlier) with "N days earlier" labels and change since first photo, per-frame stats in the viewer sequence. |
| M6 Snapchat | **built, unverified on a real export** | Scanner (finds `memories_history.json`, classifies main / overlay / video), UTC→local date matching (exact timestamp when one entry per day, else day-level), resumable two-phase job (hash + register, then score in batches of 32 with ETA), CLIP zero-shot + pose score + thresholds, auto-accepted files copied with Snapchat dates, review queue page (A/R/J/K/U/Enter, score chips, bands, accept-all-above, score histogram with thresholds), undo, ledger keeps rejected files out of later imports. Tested with a synthetic export and a mocked CLIP. **Parser written against Snapchat's documented layout, not the real export (Q3).** CLIP weights download in progress (slow link). Threshold calibration waits for the real export. |
| M7 Polish | **partly done** | Done: README, error states for missing models / unreadable files, HTTP caching for derived images, SQLite WAL + indices, paginated library, `start.ps1` honors `PORT`, schema migration table (v2 adds `import_candidates.prev_decision`). Open: browser click-through of every page, grid virtualization for very large libraries (currently 120 per page + lazy images), performance check on a 1,000-photo library. |
| S1 Server: Docker + login | **built; deploy waits on Docker install** | Server mode (`PL_MODE=server`) of the same code: argon2 login, cookie sessions, rate limit, Origin check, CSP, `Dockerfile` + `docker-compose.yml` (127.0.0.1:8420, CPU/memory caps, lower job priority), `docs/server-setup.md`, `docs/gpu-setup.md`, CI in both modes. Next: S2 upload + auto-processing, S3 GPU, S4 mobile/PWA, S5 capture, S6 daily, S7 live pose (see CHANGELOG 2026-11-23). |

Deviations and findings so far:
- Project lives at `C:\Users\dnlon\Downloads\PhysiqueLapse` (where the plan already was), not the Q1 default path.
- Uploads use a staging "upload session" (`/api/imports/upload-session` → files in batches → commit) so a large drop becomes one resumable import job. `import_sessions.thresholds` is now `options`. An `exports` table was added for M4.
- Datetimes are stored as naive local time. SQLModel 0.0.48 rejects naive datetimes by default, so model fields are typed `NaiveDatetime`.
- Head anchor in `auto` mode is the mean of nose, eyes and ears **always** (MediaPipe estimates hidden points), not "visible points only": a definition that changes with visibility made the anchor jump between photos. Fewer than 2 visible still sets `low_anchor_confidence`.
- Side-pose naming: "Left" = the subject's left side faces the camera. A mirror selfie shows the opposite side, so use the flip toggle (or "normalize facing") for mirror shots.
- Jobs must commit before reporting progress (SQLite allows one writer); the classify job deadlocked until this was fixed.
- Ingest walks folders in sorted order. When an exact file and a near-duplicate both appear, whichever comes first is kept; the other is listed under the import's details with "keep anyway".
- Windows PyPI torch wheels are CPU-only, so no special index is needed on this machine; `start.ps1` still adds the CPU index for other setups.
- Export timing: photo *i* starts at output frame round(i × 30 / fps), so any fps gives an exact total length; the last photo lingers one extra hold.
- Metric chart is a small hand-written SVG (single series, hover tooltip, synced marker) instead of a chart library, to keep the bundle small.
- Deleting a photo used to overwrite the ledger's source path with the app's own copy; fixed so "undo" and "accept" can always re-read the original source file.
- Snapchat review undo is a single-level stack per candidate (`prev_decision`), walked newest first.

### Real-data run, 2026-11-09 (user's `lifting photos.zip`: 126 photos + 40 videos; videos ignored)
- Import: 122 imported, 4 near-duplicates skipped, 119 dated from EXIF, 3 (screenshots/downloads without EXIF) from file time and flagged low-confidence. 19 s.
- Landmarks: 121/122 detected (the one miss is a car photo). 27 s on CPU (heavy model).
- **Finding: almost all photos are mirror selfies** (phone in one hand), which the five bodybuilding rules from the brief cannot describe: 79/122 went to Other. **Change:** three built-in categories with rules were added — *Mirror Selfie - Front* (other arm hanging), *Mirror Selfie - Flex* (other arm flexed), *Mirror Selfie - Side* (body turned) — gated on a "phone hand" detector (elbow < 55°, wrist between chest and face, close to the body). Double-bicep rules now also require both hands away from the body, so a one-arm flex with a phone is no longer a double bicep. Result: 75/75 agreement with my visual labels on the photos I labelled (tuned on the same photos, so this measures fit, not generalisation); needs-review dropped from 23 to 6. Existing libraries get the new categories on startup.
- **Finding: "earliest photo" is a bad default reference.** The earliest side photo had the head cropped at the frame edge and a different pose: 46/53 side photos hit the rotation clamp, median residual 235 px. **Change:** the default reference is now the category medoid (pose features + framing: head position, torso size, torso angle). Side median residual 235 → 27 px; front-double-bicep 15 px, flex 15 px, front selfie 16 px median (canvas 1080×1350). Visual check of aligned frames: heads and body scale line up within each category.
- Remaining misclassifications seen (first pass): a group shot with someone else flexing (→ Front Double Bicep), an arms-crossed shot and one flex landing in Side. Fix in the Library; corrections feed kNN once 8 labels exist.
Local-only physique progress tracker: upload → date → pose → align → time-lapse, with stats and a Snapchat Memories importer.


### Second pass, 2026-11-09 (user asked for better alignment and finer categories)
- **Categories now:** Front Double Bicep, Back Double Bicep, Front Relaxed, **Front Lat Spread** (hands on the waist, elbows flared: elbow-to-midline ≫ wrist-to-midline), Side Arm Down Flex L/R, Mirror Selfie - Front, Mirror Selfie - Flex, **Mirror Selfie - Side (Arm Down)** (free arm straight, elbow > 135°, wrist below the hips), **Mirror Selfie - Side (Arm 90)** (free arm bent 55–125°), Other. The old single side-selfie category is migrated to "Arm Down" on startup; new built-ins are inserted into existing libraries. Stage poses (double biceps, lat spread, relaxed, side) are now ruled out when a phone hand is detected. Features v2 adds elbow-to-midline distance (L/R); cached features recompute automatically.
- On the 122 photos: Lat Spread 2 (both correct), Side Arm Down 19, Side Arm 90 37, Front 34, Flex 17, Double Bicep 8, Other 5; 4 need review.
- **Alignment method "body" (new default, per category; "pin head" still available):** weighted least-squares similarity fit (2-D Umeyama, no reflection) over nose, eyes, ears, shoulders (×2) and hips (×1.5), using only points visible in both photos; arms are excluded because they move between flexes. Falls back to the head anchor when fewer than two torso points are shared. Rotation clamp still applies; scale/translation are re-solved for the clamped angle.
- **Default reference = best-fit photo:** every candidate is tried as the reference, every other photo is fitted onto it (body fit + clamp), and the one with the smallest total leftover error wins (capped per photo so one wild photo can't dominate; ≤ 120 candidates). Replaces the medoid, which picked an upright early side photo while most later ones lean 12–20°.
- **Rotation clamp default 8° → 15°:** mirror selfies tilt 10–20°; with ≥ 4 torso points the fitted angle is reliable.
- Median / p95 residual in px on a 1080×1350 canvas (shoulder-, hip-, eye-mid), real photos:

| Category | n | pin head, 8° | body fit, best-fit ref, 15° |
|---|---|---|---|
| Front Double Bicep | 7 | 14.8 / 20.0 | 4.9 / 7.2 |
| Mirror Selfie - Front | 33 | 16.3 / 67.2 | 9.1 / 17.1 |
| Mirror Selfie - Flex | 16 | 15.1 / 40.4 | 5.7 / 13.2 |
| Mirror Selfie - Side (Arm Down) | 18 | 13.7 / 25.2 | 5.8 / 12.8 |
| Mirror Selfie - Side (Arm 90) | 34 | 27.1 / 76.0 | 10.5 / 27.4 |

  Caveat: the residual is measured on the points the body fit optimises, so it favours body fit by construction; the aligned contact sheets were also checked by eye and show heads and torsos lining up within each category (the remaining visible outliers are misclassified photos, e.g. #43, #117).
- Schema v3 adds `pose_categories.align_method`. 111 tests pass.

### Snapchat zips, 2026-11-09 (user is downloading their Memories as zip files)
- **Import page → Snapchat Memories:** drop the export's .zip files (streamed to disk with per-file progress, no temp copy, multi-GB OK) or point at the folder that holds them (read in place, faster). Zips are listed with photo/video counts and whether they carry `memories_history.json`.
- **Unpack and filter** (one resumable job, `snapchat_zip_import`): extracts only photos (skips videos and `-overlay` caption layers, opens nested per-memory zips), plus `memories_history.json`; then runs the existing Snapchat import (UTC→local date matching, CLIP + pose score, auto-accept / review / auto-reject, near-duplicate skip). Uploaded zip copies are deleted after unpacking; zips read in place are never modified. Extracted photos stay in `data/incoming/snapchat/<id>/` until "Free space" (refused while photos still wait for review unless confirmed).
- A failed, cancelled or restart-interrupted import is released and shows **Resume** (skips zips already unpacked and photos already scored).
- **Filter check on real data:** on the user's 120 lifting photos the current prompts score 118 as auto-accept (median CLIP 1.00), the car photo is rejected, a screenshot goes to review. End-to-end on a scratch data dir with a Snapchat-style zip (20 of those photos + 10 synthetic non-photos + overlays + a video): 19 imported, 1 near-duplicate skipped, 10 rejected, overlays/video never extracted, 16 s. **Not yet measured: rejection of real non-physique snaps** (food, friends, screenshots) — check the score histogram on the Review page after the first real export and adjust the thresholds in Settings.
- Fixed while testing: CLIP is now loaded from a flat file `data/models/clip_vit_b32_laion2b.safetensors` (the Hugging Face cache path exceeded Windows' 260-character limit when the data folder is deep, and its offline lookup fell back to a file name that was never downloaded). 113 tests pass.

### Body sizes from photos, 2026-11-09
- **Approach (chosen over a calibration face photo):** MediaPipe's own body-outline mask (`output_segmentation_masks`, same model, no download) + landmarks. Each size is a 2-D width across the outline divided by the torso length T in the same photo (the skeleton doesn't grow, so T is a per-photo ruler). A face photo was rejected as the ruler: it can't set the scale of other photos, the phone covers the face in mirror selfies, and interpupillary distance is only 30–60 px in a full-body shot.
- **Measurements:** shoulder width (widest delt run 0.04–0.16 T below the shoulder line; refused if an arm is raised along it), lat width (0.30 T below; refused if the outline reaches an arm), waist (0.82 T; same rule), V-taper (shoulders / waist), upper arm (perpendicular to the humerus at 45–65 %, bigger valid side; refused if the run merges with the torso), forearm (20–40 % from the elbow; refused if folded/holding the phone, foreshortened, or touching the torso — marked low confidence). Every refusal carries a reason that the overlay shows.
- **Storage:** masks in `data/derived/masks/{id}.png`; table `photo_measurements` (`data` JSON per measurement: valid, ratio, px, line, why). Landmark extraction now saves the mask and measures; `measure_photos` job (re)measures and re-runs the model only for photos that have no mask yet.
- **Charts are per pose type** (a flex and a relaxed shot measure differently); default pose per measurement from a preference list (front shots for torso widths, flexes for arms). Trend = centered rolling median (21 days, setting `measure_smooth_days`).
- **Real units (optional):** log tape measurements named Arm / Forearm / Waist / Shoulders / Chest on the Stats page; inches = k × ratio, k fitted by least squares on one pair per tape entry (closest trend value within the stats window). Shows the fit error.
- **Real-data check (119 photos):** same-day spread (photos minutes apart, so the body can't have changed) in each measurement's preferred pose: shoulders 2.6 % (front selfie), waist 1.0–2.4 %, lats 2.6 % (double bicep), upper arm 1.7 % (flex) / 2.7 % (double bicep), V-taper 2.5 %, forearm 4.5 %. Side-pose widths are noisier (4–9 %). Overlays checked by eye: lines land on the delts, under the armpits, at the waist and across the biceps peak.
- **Caveats:** clothes are part of the outline (tank top vs shirtless changes torso widths); camera distance and height change perspective in mirror selfies, so long-term trends can include framing drift; forearm is rarely clear of the body in these photos. Tests: 9 new (synthetic outlines with known widths, rejection rules, scale invariance, trend, calibration, API); 122 total.
- UI: Stats → **From photos** tab (one card per size: dots = photos, line = trend, pose picker, click a dot for the overlay), photo drawer **sizes** button, Viewer shows the frame's sizes under the stats strip.

### Joints and sides, 2026-11-09 (user: shoulders, elbows, wrists should land in the same spots; split groups by side)
- **No warping, on purpose.** A similarity transform (move, scale, rotate) cannot put every joint on the same pixel when the arm is held at a slightly different angle; only a non-rigid warp can, and a warp stretches the body, i.e. fakes the growth the time-lapse is meant to show. So: fit the joints as closely as one rigid move allows, measure what's left, and let the user filter by it.
- **New default alignment "joints"** (per category; "body" and "pin head" still selectable): weighted least squares over shoulders 2, elbows 1.5, wrists 1, hips 1, nose 0.5, eyes/ears 0.25. Reference selection uses the same weights. Schema v4: `alignments.joint_residual_px`, existing categories switched from body → joints, alignments marked stale.
- **Pose match:** per photo, mean shoulder/elbow/wrist distance to the reference after alignment, shown as % of torso length (`pose_diff_pct` in the sequence; Editor shows px). Viewer → "Pose match" filter (all / ≤12 % / ≤8 % / ≤5 % / ≤3 %), remembered per browser; Export uses the same limit.
- **Real photos, median joint distance to the reference (canvas px), body fit → joints fit:** Front Double Bicep elbow 56 → 14, wrist 46 → 22; Flex elbow 24 → 10, wrist 28 → 16; Side Arm 90 elbow 63 → 26, wrist 50 → 43; Side Arm Down elbow 21 → 16, wrist 33 → 25; shoulders 9–15. Median pose difference per category 2.8–6.4 % of torso length. Checked visually with the reference joints drawn as crosses on aligned frames.
- **Left/right groups:** Mirror Selfie Front split by phone hand, Flex by flexed arm, Side (Arm Down) and Side (Arm 90) by the side facing the mirror (the stage Side Arm Down Flex already was). Names use the subject's REAL side: in a mirror selfie the image is mirrored and the pose model labels it as if it weren't, so real side = opposite of the model label; equivalently, the arm on the image's right is the real right arm. Flipping a photo in the app moves it to the other side group. Old categories are migrated to the "right" half (reference cleared); hand-assigned photos keep their pose but follow their actual side on the next Classify.
- On the 120 photos: Front (phone left hand) 32 / (right) 1, Flex (right arm) 16 / (left) 1, Side Arm Down left side 19, Side Arm 90 left side 35 / right side 2 (one genuine, one hand-assigned car photo). 125 tests pass.

### Abs group, 2026-11-09 (user: a group of every photo with visible abs, to see ab progress)
- **An attribute, not a pose category:** a photo keeps its pose group and can also be in Abs (`photos.abs_visible / abs_score / abs_source / abs_version`, schema v5). Manual override (`abs_source = manual`) is never overwritten by detection; "reset" returns to automatic.
- **Detection (`detect_abs` job):** CLIP zero-shot on a crop of the torso (shoulders to just below the hips, after the photo's flip): bare stomach / visible abs vs. tank top / t-shirt / covered torso; threshold `abs_threshold` = 0.5. On the 119 real photos the scores split cleanly: 11 shirtless photos 0.98–1.00, two "tank top pulled up" photos 0.87 and 0.80, everything else ≤ 0.18 (all covered torsos) → 13 found, all correct by eye. 24 s.
- **Close-ups:** one rigid move per photo (two-point similarity: shoulder-mid → 10 %, hip-mid → 86 % of a 1080×1080 canvas; no warp), stored in `data/derived/abs/{id}.jpg`, so the abdomen sits in the same place and at the same scale relative to torso length in every frame, across pose types.
- **UI:** Viewer pose picker → "Abs (close-ups from every pose)" with Find abs, playback, onion/split and export (`/exports` with `group: "abs"`); Library "abs visible" filter and an "abs" badge on cards; drawer "Abs visible" switch with detected % / set-by-you · reset; Dashboard "Find abs" and "Abs" buttons. 127 tests pass.
---

## 0. Environment findings (this machine, checked 2026-11-09)

| Item | Finding | Consequence |
|---|---|---|
| OS / hardware | Windows 11 Home, i7-12700H (14 cores / 20 threads), 15.7 GB RAM, RTX 3050 Ti Laptop (4 GB) | CPU inference is the plan; comfortably fast enough. GPU optional later. |
| Python | 3.12.2 at `C:\Python312`, pip 24.0 | MediaPipe wheels exist for 3.12. |
| Node | 21.6.2 / npm 10.2.4 | Node 21 is end-of-life. Vite 7 requires Node ^20.19 or >=22.12, so it will **not** run. Vite 6 runs on 21 with an engines warning. See Q6. |
| git | 2.53 | Project will be a git repo. |
| ffmpeg | not installed | Use `imageio-ffmpeg` (bundles a static ffmpeg) for MP4; prefer system ffmpeg if found later. |
| MediaPipe (current PyPI) | 1.2.2 (2026-11-03). Installed in a scratch venv and inspected: Tasks API present (`mediapipe.tasks.python.vision.PoseLandmarker`), legacy `mp.solutions` removed, depends on `opencv-contrib-python 5.0`, numpy 2.5. | **Its `libmediapipe.dll` embeds a Clearcut logging client that uploads usage metrics to `play.googleapis.com/log`.** The package README's privacy notice says so; GitHub issues #6291 / #6397 confirm it is on by default with no opt-out API. That breaks the "no telemetry / no external calls at runtime" requirement. |
| MediaPipe 0.10.21 (2025-02-06) | Last Windows + Python 3.12 wheel before the uploader was added. I downloaded it and searched its binaries: zero matches for `clearcut` / `play.googleapis.com`. Same Tasks API shape. Pins `numpy<2`, `protobuf<5`; pulls jax, matplotlib, sounddevice (unused by us, harmless). | **Chosen** (see §1). |
| Pose model files | `storage.googleapis.com/mediapipe-models/pose_landmarker/...` reachable: lite 5.8 MB, full 9.4 MB, heavy 30.7 MB (files dated Apr 2023, unchanged across MediaPipe versions). | One-time download to `data/models/`. |
| Snapchat export | Not found under Downloads / Desktop / Documents / Pictures / OneDrive (3 levels deep, looked for `memories_history*` and `mydata*`). | Need the path (Q3). Parser will be written against the real folder. |
| Disk | 80 GB free of 455 GB | Fine (library of 1,000 photos + derived ≈ 2–4 GB). |

---

## 1. Decisions and deviations from the brief (please object if you disagree)

1. **MediaPipe pinned to `0.10.21`** because every newer release phones home and cannot be told not to. The pose model itself is identical. Risk: it is a frozen version; mitigation: a `PoseBackend` interface with an internal 17-keypoint schema (nose, eyes, ears, shoulders, elbows, wrists, hips, knees, ankles + visibility) so RTMPose (`rtmlib` + onnxruntime, no telemetry) or YOLO-pose can be dropped in without touching alignment/classification code. Landmarks are cached, so a backend swap only costs a re-extraction job.
2. **Rotation basis = torso axis (hip-mid → shoulder-mid), not the eye line.** Reason: head tilt is independent of body posture; levelling on the eyes would rotate the whole body whenever you tilt your head, and the eye line is foreshortened in side poses. The torso axis is visible in every pose. Eye line / shoulder line / none remain selectable per pose category, and rotation is clamped (default ±8°).
3. **Mirror detection is not reliably possible** without reading mirrored text, so I will not pretend to detect it. Instead: a per-category "normalize facing direction" option that auto-flips photos whose landmark-derived facing differs from the reference, plus the per-photo flip toggle and bulk flip. Flips are applied consistently to the image, its landmarks, its features and its alignment.
4. **Reference frame semantics.** The reference photo is placed fit-to-canvas (optionally zoomed/offset per category). Its head anchor position, torso length and torso angle on the canvas become the targets; every other photo is mapped onto those targets with a similarity transform. Changing the reference re-aligns the category.
5. **Scale fallback** when hips are not visible (visibility < 0.5): head size = mean of inter-ocular and inter-ear pixel distances, relative to the same measure on the reference. Flagged `scale_from_head` in the UI; the manual editor fixes the rest.
6. **"Add folder by path"** in addition to drag-and-drop. The server runs on your machine and can read your folders directly; pushing 5,000 files through the browser is slow and fragile. Drag-and-drop stays for ad-hoc uploads.
7. **PyTorch CPU wheels** (~200 MB) by default. CUDA build optional later; nothing in the workload needs it.
8. **MP4 via `imageio-ffmpeg`** (bundled ffmpeg binary), GIF via Pillow. System ffmpeg is used if present.
9. **Frontend libraries beyond the brief:** `react-router` (pages) and `@tanstack/react-query` (polling jobs, caching). Tailwind v4 via `@tailwindcss/vite`. Vite 6 unless Node is upgraded (Q6).
10. **Deleting a photo** moves *our copy* to `data/trash/` and removes derived files; your source files are never touched. Deleted hashes stay in the ingest ledger so a re-import does not resurrect them unless you ask.
11. **Snapchat dates are UTC** in the export; they are converted to local time (system timezone by default, overridable in Settings).
12. **Model caches live inside the project:** `data/models/pose_landmarker_heavy.task` and `data/models/open_clip/` (I set `cache_dir` explicitly so nothing lands in `~/.cache` silently). Both downloads happen once during setup (`scripts/download_models.py`), not at runtime.
13. **Only three things ever make network calls, all during setup:** pip/npm, the two model downloads above. The app binds to `127.0.0.1` only.

---

## 2. Architecture

```
 Browser (React/Vite/TS/Tailwind)  ──HTTP /api──▶  FastAPI (uvicorn, 127.0.0.1:8000)
   pages: Dashboard · Library · Import              │  routers → services → SQLite (SQLModel)
          Review · Viewer · Stats · Settings        │  JobRunner (threads) ─▶ CV services
   polls /api/jobs every 1 s while jobs run         │     landmarks (MediaPipe) · CLIP (open_clip)
                                                     │     align (OpenCV) · export (imageio-ffmpeg)
                                                     ▼
                                              data/  originals/ · derived/ · models/ · physiquelapse.db
```

- **One process.** FastAPI serves `/api/*` and, in normal use, the built frontend from `frontend/dist`. Dev mode runs Vite on 5173 with a proxy to 8000 (hot reload).
- **Jobs.** `JobRunner` owns two worker threads: `cv` (landmarks, CLIP, alignment, export; one at a time because the models are not thread-safe and the CPU is better used by one batched job) and `io` (ingest, thumbnails, hashing). Every job is a row in `jobs` with `done/total/message`; the UI shows a progress drawer. Jobs are **idempotent and resumable**: each one derives its work list from DB state ("photos without landmarks", "candidates without a decision"), so after a crash or restart you click Run again and it continues. Cancellation is cooperative (checked between items).
- **Models** load lazily and stay resident (singletons). First use of a model shows "loading model…" in the job message.
- **Processing pipeline per photo:** ingest (copy original, sha256, EXIF orientation, phash, date, thumb, preview) → landmarks (cached JSON) → features → classification → alignment (per category, relative to that category's reference) → viewer sequence.
- **Flip handling:** landmarks are always cached for the EXIF-corrected, un-flipped image. `flip_horizontal` is applied on read (mirror x, swap left/right indices) before features, classification and alignment, so toggling a flip never requires re-running the model.

---

## 3. Folder structure

```
PhysiqueLapse/
├─ PLAN.md  CHANGELOG.md  README.md
├─ start.ps1            # one command on Windows: setup (first run) + build + serve; -Dev for hot reload
├─ start.sh  Makefile   # same for macOS/Linux (make dev / make test / make eval)
├─ .gitignore           # data/, .venv/, node_modules/, frontend/dist/, samples/
├─ backend/
│  ├─ pyproject.toml    # deps + [rembg] extra; pytest config; ruff
│  ├─ app/
│  │  ├─ main.py        # app factory, static mount, lifespan (db init, job runner)
│  │  ├─ config.py      # paths (DATA_DIR env override), defaults
│  │  ├─ db.py          # engine/session, create_all, tiny migration table
│  │  ├─ models.py      # SQLModel tables (§4)
│  │  ├─ schemas.py     # request/response models
│  │  ├─ jobs.py        # JobRunner, Job base, progress reporting, cancellation
│  │  ├─ api/           # one router per area: photos, imports, poses, alignment, viewer, exports, metrics, review, settings, dashboard, jobs
│  │  └─ services/
│  │     ├─ ingest.py        # copy, hash, orientation, thumbs, dedupe, ledger
│  │     ├─ dates.py         # EXIF / snapchat / filename / mtime resolution
│  │     ├─ snapchat.py      # export scanner + parser (written against the real export)
│  │     ├─ pose_backend.py  # PoseBackend interface, MediaPipe impl, keypoint schema
│  │     ├─ landmarks.py     # extraction job, cache, flip transform
│  │     ├─ features.py      # landmark → feature vector
│  │     ├─ classify.py      # rules + kNN + confidence
│  │     ├─ align.py         # similarity transform, canvas render, residuals
│  │     ├─ physique_filter.py # CLIP zero-shot + pose signal → score/decision
│  │     ├─ clip_backend.py  # open_clip wrapper, batching, cache_dir
│  │     ├─ export_video.py  # frames → MP4/GIF with captions
│  │     ├─ stats.py         # nearest-date matching, CSV parsing, unit conversion
│  │     └─ images.py        # open any format (HEIC via pillow-heif), EXIF transpose, resize
│  ├─ scripts/
│  │  ├─ download_models.py  # pose .task + CLIP weights → data/models/ (idempotent)
│  │  ├─ eval_alignment.py   # alignment residual report per pose category
│  │  └─ make_sample_set.py  # generates the synthetic test fixtures
│  └─ tests/                 # pytest; fixtures are synthetic (see §9)
├─ frontend/
│  ├─ package.json  vite.config.ts  tsconfig.json (strict)  index.html
│  └─ src/
│     ├─ main.tsx  App.tsx (shell: sidebar nav, job drawer, toasts)
│     ├─ api/   client.ts (typed fetch) · types.ts (mirrors schemas.py) · queries.ts (react-query hooks)
│     ├─ pages/ Dashboard · Library · Import · ReviewQueue · Viewer · Stats · Settings
│     ├─ components/ PhotoGrid · PhotoCard · PhotoDrawer · DateEditor · BulkBar · PoseBadge ·
│     │              LandmarkOverlay · AlignmentEditor · Scrubber · OnionSkin · CompareSlider ·
│     │              StatsStrip · MetricChart · Uploader · JobProgress · ScoreBadge · Shortcuts
│     └─ lib/   keyboard.ts · format.ts (dates, units) · preload.ts (image cache for the viewer)
└─ data/                      # created at first run; gitignored; DATA_DIR env can relocate it
   ├─ originals/              # untouched copies, named <sha256[:16]>.<ext>
   ├─ derived/thumbs/  previews/  landmarks/  aligned/  exports/
   ├─ models/                 # pose_landmarker_heavy.task, open_clip/
   ├─ trash/
   └─ physiquelapse.db
```

---

## 4. Database schema (SQLite via SQLModel; metadata only, no pixels)

```
photos
  id PK · sha256 UNIQUE · phash (16 hex) · original_path · original_filename · ext
  source (upload|folder|snapchat) · source_ref (import_candidate id / folder) · width · height (after EXIF orientation) · exif_orientation
  taken_at DATETIME NULL · taken_at_source (exif|snapchat_metadata|filename|file_mtime|manual|none) · taken_at_confidence (high|medium|low)
  flip_horizontal BOOL · flip_source (manual|auto|none)
  pose_category_id FK NULL · pose_confidence REAL · pose_source (rule|knn|manual|none) · pose_needs_review BOOL
  landmarks_status (pending|ok|none|failed) · landmarks_quality REAL (mean visibility of core points) · landmarks_path
  excluded BOOL (hide from viewer without deleting) · notes · created_at · updated_at

pose_categories
  id PK · name UNIQUE · sort_order · reference_photo_id FK NULL
  anchor_mode (auto|face|ears) · scale_basis (torso|nose_hip|head) · rotation_basis (torso|eyes|shoulders|none)
  normalize_facing BOOL · framing_zoom REAL (1.0) · framing_dx · framing_dy (canvas fractions) · rule_key NULL (builtin rule set) · created_at

pose_features        photo_id PK · vector JSON · version INT · computed_at        # for the photo's current flip state
pose_labels          id PK · photo_id UNIQUE · pose_category_id FK · vector JSON · created_at   # your corrections = kNN training set

alignments
  photo_id PK · reference_photo_id · canvas_w · canvas_h · scale · rotation_deg · tx · ty
  method (auto|manual) · scale_basis_used (torso|nose_hip|head) · flags JSON (scale_from_head, low_anchor_confidence, rotation_clamped, no_landmarks)
  residual_px REAL · output_path · stale BOOL · computed_at

metrics              id PK · name UNIQUE · unit · is_builtin · sort_order · decimals
metric_entries       id PK · metric_id FK · date DATE · value REAL · note · UNIQUE(metric_id, date)

jobs                 id PK · type · status (queued|running|done|failed|cancelled) · done INT · total INT · message · params JSON · result JSON · error · created_at · started_at · finished_at

import_sessions      id PK · kind (snapchat|folder|upload) · root_path · status · counts JSON (files, images, videos_skipped, overlays_skipped, accepted, rejected, review, duplicates, errors) · thresholds JSON · created_at · finished_at
import_candidates    id PK · session_id FK · file_path · file_name · sha256 UNIQUE · phash · source_date · date_source · media_type
                     clip_score · pose_score · score · decision (pending|auto_accept|auto_reject|review|accepted|rejected|duplicate|error) · decided_by (auto|user) · photo_id FK NULL · error · created_at · decided_at
                     # this is also the ledger: rejected/duplicate files are recorded here and never copied; a re-import skips known sha256s

settings             key PK · value JSON   # canvas_w/h, units, thresholds, weights, prompts, stats_window_days, rotation_clamp_deg, knn_k, knn_min_labels, review_confidence, timezone, bg_removal, playback defaults, schema_version
```

Landmark JSON (`data/derived/landmarks/{photo_id}.json`): `{model, image_w, image_h, points: [{name, x_px, y_px, z, visibility, presence}] × 33, detected: bool}` for the EXIF-corrected, un-flipped image.

---

## 5. API (all under `/api`, JSON; file bytes served with cache headers)

```
GET  /health                                   GET /dashboard
GET  /settings · PUT /settings                 GET /jobs?active=1 · GET /jobs/{id} · POST /jobs/{id}/cancel

POST /uploads                                  multipart, many files (+ relative paths for folders) → job
POST /imports/folder {path, recursive}         server-side folder import → job
GET  /photos?pose_id&from&to&source&date_source&needs_review&flagged&q&sort&page&page_size
GET  /photos/{id} · PATCH /photos/{id}         {taken_at, flip_horizontal, pose_category_id, excluded, notes}
POST /photos/bulk {ids, patch | date_shift_days | rederive_date_from}   DELETE /photos/{id} (→ trash)
GET  /photos/{id}/original | /thumb | /preview | /aligned | /landmarks
POST /photos/{id}/reprocess {landmarks|align}

GET  /poses · POST /poses · PATCH /poses/{id} · DELETE /poses/{id} (photos → Other)
POST /poses/{id}/reference {photo_id}          POST /poses/{id}/align → job      POST /poses/classify → job (rules/kNN over non-manual photos)
GET  /poses/{id}/sequence?from&to              ordered frames: id, date, days_since_start, aligned_url, flags, stats_at_date
GET  /poses/{id}/eval                           alignment residual summary (same numbers as scripts/eval_alignment.py)

GET  /alignments/{photo_id} · PUT /alignments/{photo_id} {scale, rotation_deg, tx, ty | anchors} · DELETE (back to auto)
POST /landmarks/extract → job                   (all photos missing landmarks; or {ids})

POST /exports {pose_id, from, to, fps, format: mp4|gif, width, crossfade, caption: {date, days, metrics[]}} → job
GET  /exports · GET /exports/{id}/download · DELETE /exports/{id}

GET  /metrics · POST /metrics · PATCH /metrics/{id} · DELETE /metrics/{id}
GET  /metrics/entries?metric_id&from&to · POST /metrics/entries (upsert) · DELETE /metrics/entries/{id}
POST /metrics/import-csv (multipart; dry_run=1 returns a preview) · GET /metrics/sample-csv
GET  /stats/at?date&window_days                nearest entry per metric with offset label, change since first photo

POST /snapchat/scan {path}                      no side effects: counts + sample filenames + which metadata file was found
POST /snapchat/import {path} → job              resumable; auto-accept / auto-reject / review per thresholds
GET  /review?session_id&decision=review&page   · POST /review/{candidate_id} {accept|reject} · POST /review/bulk {ids, decision} · POST /review/undo
```

---

## 6. CV design

### 6.1 Landmarks
- MediaPipe Tasks `PoseLandmarker`, `pose_landmarker_heavy.task`, IMAGE mode, `num_poses=1` (2 for the physique filter to detect group shots), confidences 0.5. Input: EXIF-corrected RGB, long side resized to ≤ 1280 px for speed (coordinates scaled back to original pixels).
- Optional **flip test-time augmentation** (run on the mirrored image too, mirror back, average): halves landmark jitter for ~2× cost. Off by default, switchable in Settings; the eval script tells us if it is worth it on your photos.
- Visibility gates: a landmark counts as visible at `visibility ≥ 0.5`. `landmarks_quality` = mean visibility over nose, shoulders, hips.

### 6.2 Features, rules, kNN
Feature vector (all scale/translation invariant, computed on the flipped-as-displayed landmarks, torso length `T` = |shoulder_mid − hip_mid| as the unit):
elbow angle L/R · shoulder abduction L/R (upper arm vs. torso axis) · wrist height rel. shoulder L/R (÷T) · wrist lateral distance from midline L/R (÷T) · shoulder width ÷T · hip width ÷T · shoulder/hip width ratio · facing score (nose offset from shoulder mid ÷ shoulder width, ear visibility difference, shoulder z difference) · face visibility (mean of nose/eyes) · knee/ankle visibility (framing).

Rules (starter categories; each returns a 0–1 score from smooth thresholds, winner = argmax, confidence = top score scaled by the margin to the runner-up):
- Front Double Bicep: both elbows < 110°, both wrists above shoulders, face visible, shoulder width ÷T > 0.45.
- Back Double Bicep: same arms, face not visible.
- Front Relaxed: elbows > 140°, wrists below hips, face visible, shoulders wide.
- Side Arm Down Flex (Left / Right): shoulder width ÷T < 0.35, near arm straight (> 150°) with wrist near hip level, side from the facing score.
- Other: nothing above 0.5. `needs_review` when confidence < 0.6 (setting).

kNN: standardized features; `k = min(5, n)`; distance-weighted vote; confidence = winner's weight share. Active when `≥ 8` labels spanning `≥ 2` categories exist (settings), otherwise rules. Every manual assignment (single or bulk) upserts a `pose_labels` row; a "Reclassify" job re-runs kNN over all non-manual photos and reports how many changed. User-created categories are learnable only through kNN (rules do not know them), which is stated in the UI.

### 6.3 Alignment (similarity transform; the heart of the app)
Canvas `W×H` (default 1080×1350). Default reference = category medoid (see real-data run in the status section). For the reference `R`: `s_R = min(W/w_R, H/h_R) × framing_zoom`, `t_R` centres it (+ framing offsets). Targets on the canvas: `head_T`, `torso_T = |shoulder_mid − hip_mid|`, `angle_T` (torso axis).
For a photo `P` (flip applied):
- anchor `head_P` = mean of visible head points (nose, eyes, ears; `anchor_mode` can force face or ears; back poses usually end up on ears). Fewer than 2 visible → `low_anchor_confidence` flag.
- scale `s = torso_T / torso_P`; hips not visible → head-size fallback, `scale_from_head` flag.
- rotation `θ = clamp(angle_T − angle_P, ±rotation_clamp_deg)`; `rotation_clamped` flag if clamped.
- `M = s·Rot(θ)`, `t = head_T − M·head_P`; render with `cv2.warpAffine` (source pre-shrunk with INTER_AREA when `s < 1`, then INTER_LINEAR), neutral border colour, JPEG q92 → `derived/aligned/{id}.jpg`.
The head lands on `head_T` exactly by construction; residuals are measured on shoulder_mid, hip_mid and the eye midpoint (px and % of H). Manual override stores explicit `(s, θ, tx, ty)`; dragging the anchor handles instead re-runs the auto solve with corrected points. Optional background removal (`rembg`, extra) runs before warping and composites onto the neutral background.

### 6.4 Physique filter (Snapchat import)
`score = 0.65·clip + 0.35·pose` (weights in Settings).
- `clip`: open_clip `ViT-B-32 / laion2b_s34b_b79k`, zero-shot softmax (×100 logit scale) over positive prompts ("a shirtless man flexing his muscles in a mirror", "bodybuilding progress photo", "a physique pose", "a man flexing his biceps", …) vs. negatives ("food", "a landscape", "a group of friends", "a screenshot", "a selfie of a face", "a pet", "a document", "a car", …); `clip` = total positive probability. Prompt lists editable in Settings. Batched 32 at a time on CPU.
- `pose`: 0 if no person; else detection presence × fraction of core landmarks visible × size factor (person height ≥ 25 % of image → 1) × 0.5 if a second person is detected.
- Speed trick: pose only runs when `clip ≥ 0.15`; obvious non-physique images skip it. Budget on this CPU for 5,000 images: ≈ 15–25 min, resumable.
- Decision: `≥ accept_threshold (0.70)` auto-accept, `< reject_threshold (0.30)` auto-reject, else Review Queue. Initial thresholds will be calibrated on your real export in M6 by looking at the score distribution; both are sliders in Settings and the queue shows `score · clip · pose` on every tile.

### 6.5 Dates
Priority: EXIF `DateTimeOriginal` (0x9003) → `DateTimeDigitized` (0x9004) → Snapchat metadata (JSON entry, else the `YYYY-MM-DD` filename prefix) → filename patterns (`2024-03-15`, `20240315_093000`, `20240315`, `IMG_20240315`, `PXL_20240315_…`, `Screenshot_20240315-…`, `2024-03-15 09.30.00`) → file mtime (confidence `low`, badge in UI) → manual. Years restricted to 2000–2035 to avoid false matches. Every photo carries `taken_at_source` + confidence; bulk tools: set date, shift ±N days, re-derive from filename/mtime.

### 6.6 Duplicates
sha256 exact match → skipped, counted. `imagehash.phash` (64-bit) Hamming distance ≤ 6 (setting) → near-duplicate: skipped by default, listed in the import summary with a "keep anyway" action. Comparison is brute force over in-memory hashes (5,000 × 64-bit is trivial).

---

## 7. UI

Dark theme, sidebar nav, persistent job drawer (bottom-right) with progress bars, toasts for errors.

| Page | Contents |
|---|---|
| Dashboard | photo count per pose, date span, latest photos, latest weight + change, pending review count, buttons for the common jobs |
| Library | virtualized thumbnail grid; filters (pose, date range, source, date-source, needs-review, flagged, text); multi-select (click/shift/ctrl, Ctrl+A); bulk bar (assign pose, set/shift date, flip, exclude, delete); photo drawer (large preview, landmark overlay toggle, date editor with source badge, pose selector + confidence, flip, alignment flags, "set as reference", "open in editor") |
| Import | drop zone (files + folders), "add folder by path", Snapchat section (path → Scan → results → Start/Resume), import history with counts |
| Review Queue | big tiles with score chips; keyboard: `A` accept, `R` reject, `←/→` or `J/K` move, `U` undo, `Enter` zoom; filter by score band; "accept all above X" |
| Viewer | pose + date-range picker; stage with aligned frames; modes: single / onion skin (comparison picker, opacity slider) / side-by-side (draggable divider); scrubber with date, day N, photo i/N; play with speed + crossfade; stats strip under the stage; metric chart with synced marker; export dialog; keys: `←/→` step, `Space` play, `Home/End`, `[` `]` set comparison, `O` onion, `S` split |
| Stats | metric tabs, quick-add form, table with inline edit, CSV import with preview + sample download, charts |
| Settings | pose categories (CRUD, reference, anchor/scale/rotation basis, framing, normalize facing), canvas size, units, filter thresholds/weights/prompts, stats window, rotation clamp, kNN params, timezone, data folder info, model status, background removal toggle |

---

## 8. Milestones and acceptance criteria

**M1 – Skeleton**
- `.\start.ps1` on a clean checkout: creates `.venv`, installs backend + frontend deps, builds the frontend, starts the server, opens `http://127.0.0.1:8000`. `.\start.ps1 -Dev` runs uvicorn `--reload` + Vite with proxy. `make dev` / `start.sh` equivalents.
- Upload by drag-and-drop (files and a folder) and by path; JPG/PNG/HEIC/WEBP; progress shown; exact and near duplicates skipped and reported.
- Library grid with date + source badge, low-confidence highlight, filters, sort; photo drawer with date editor (single) and bulk date tools (set / shift / re-derive); flip toggle; EXIF orientation respected.
- Tests: `dates.py` (EXIF, every filename pattern, mtime fallback, priority order), dedupe thresholds, ingest naming + originals byte-identical.
- My verification: fresh run from an empty folder, 24 synthetic images incl. HEIC/WEBP, check badges, bulk shift, duplicate skip.

**M2 – Pose**
- Landmark extraction job with progress; cached; re-run only processes missing photos; model download script works and reports the cache location.
- Feature vectors + rule classification for the 5 starter categories (+ Other) with confidence and `needs_review` flag/filter; landmark overlay in the drawer.
- Pose correction single + bulk; each correction stored as a label; kNN activates at the configured label count; Reclassify job; source badge rule/kNN/manual; categories CRUD.
- Tests: synthetic landmark fixtures per pose → expected rule output; flip invariance (features of a flipped front pose match; side pose L↔R swaps); kNN voting + activation fallback. Model smoke test (runs only if the `.task` file exists) on a sample image.

**M3 – Alignment**
- Reference per category (default earliest; "set as reference"); align job; outputs on the canvas with neutral padding; flags shown in Library and Viewer.
- Manual editor: reference ghost under the photo, opacity slider, drag / wheel-zoom / rotate (Shift = fine), draggable head and hip anchors, save/reset, before-after toggle.
- `python scripts/eval_alignment.py --pose "Front Double Bicep"` prints per-photo residuals and mean / median / p95 (px and % of canvas height) plus flag counts; same numbers at `GET /poses/{id}/eval`.
- Tests: synthetic landmarks under random similarity transforms are recovered (head residual < 0.01 px, torso residual < 1e-6 of T); rotation clamp; head-size fallback path; manual override round-trip.

**M4 – Viewer**
- Scrubber, play (0.5–10 fps, crossfade), onion skin, side-by-side, keyboard map, preloading (stepping is instant for 1,000 frames).
- Export MP4/GIF job with fps / size / range / crossfade / caption options; downloadable from the Exports list; works without a system ffmpeg.
- My verification: 30-frame synthetic sequence exported to MP4 and GIF; files open; caption burned in.

**M5 – Stats**
- Metrics CRUD (weight built in; body fat %, waist, arm, chest presets; custom name+unit), quick-add, CSV import long + wide with dry-run preview, sample CSV, lb/kg setting converting stored weight.
- Viewer strip shows values for the frame's date with nearest-label (e.g. "182.4 lb · 2 days earlier") inside the configurable window, plus change since first photo; chart marker synced to the frame.
- Tests: nearest matching (exact, earlier, later, outside window, ties), CSV both formats + bad rows, unit conversion.

**M6 – Snapchat import**
- Scan → counts (images, videos skipped, overlay files skipped, metadata matched) → Start; resumable (stop, restart, continues); ETA; summary.
- Physique filter with thresholds, Review Queue with keyboard flow and score chips; accepted files copied with Snapchat dates; rejected never copied; re-import skips known files.
- Tests: synthetic export fixture (JSON + `-main`/`-overlay` pairs + a video) → parser output; decision logic with mocked model scores; resumability (kill mid-way, restart, no duplicates).
- Calibration: histogram of scores on your real export; thresholds adjusted with you.

**M7 – Polish**
- README (setup, model cache locations, data folder, troubleshooting, backup = copy `data/`), Settings complete, error states (missing model, unreadable file, unsupported format), performance pass (pagination/virtualization, HTTP caching for derived images, SQLite indices, WAL), CHANGELOG up to date.

---

## 9. Test strategy and sample set
- **Unit tests are synthetic and model-free:** generated images (gradients with EXIF written by Pillow, HEIC via pillow-heif, near-duplicate variants), landmark fixtures (hand-built 33-point sets per pose and random similarity transforms of them), a fake Snapchat export folder, CSV strings. Model calls are mocked in these tests.
- **Model smoke tests** (`-m models`) run only when the model files are present: pose detection on a sample image, CLIP scoring ordering (physique prompt > food prompt on a sample).
- **Visual checks I cannot automate** (I will tell you exactly what to look at each time): alignment quality on real photos, filter calibration, side pose L/R facing, mirror flips.
- Sample images: see Q4. Without real photos I can only validate the CV pipeline on synthetic or public-domain images, which is weaker than your actual data.

---

## 10. Risks and mitigations

| Risk | Plan |
|---|---|
| MediaPipe 1.x telemetry | Pin 0.10.21 (verified clean). Backend interface so RTMPose can replace it. Pin written in `pyproject.toml` with a comment explaining why. |
| 0.10.21 dependency pins (`numpy<2`, `protobuf<5`, opencv-contrib 4.x) clash with something | Resolve at M2 install; everything else in the stack supports numpy 1.26. If it clashes anyway → switch to RTMPose. |
| Landmark jitter spoils alignment | heavy model, 1280 px input, optional flip-TTA, torso-based scale, eval script to quantify; manual editor as the backstop. |
| Back poses have no face for the anchor | ears / estimated head points with a flag; eval numbers will show how bad it is. |
| Zero-shot CLIP thresholds wrong for your photos | scores visible, sliders, review queue, calibration on the real export. |
| Snapchat export layout differs from expectations | parser written against your real folder; `scan` endpoint shows what it found before anything is imported. |
| Node 21 | Vite 6 now; upgrade to Node 22 LTS recommended (Q6). |
| rembg install weight (onnxruntime + 170 MB model) | optional extra, off by default. |
| Large imports (5,000 files) in the browser | path-based import; batched uploads; virtualized grid; pagination. |
| Session resumption | CHANGELOG.md progress notes + this plan; DB state drives all jobs. |

---

## 11. Open questions (answer in one line each; "defaults" accepts every default)

1. **Project folder** — default `C:\Users\dnlon\PhysiqueLapse` (created for this file). Elsewhere?
2. **Pose model** — default: MediaPipe pinned to 0.10.21 (no telemetry). Alternatives: MediaPipe 1.2.2 (sends usage metrics to Google, no opt-out) or RTMPose via rtmlib/onnxruntime.
3. **Snapchat export** — where is the unzipped folder, and does it contain the media files (a `memories/` folder) or only the JSON with download links? May I inspect it now so the parser is written against the real layout?
4. **Sample photos** — may I use ~10 of your real photos (a gitignored `samples/` folder, never committed, never in automated tests) to validate landmarks, alignment and classification visually? Alternatively I download 2–3 public-domain bodybuilding photos from Wikimedia Commons for the model smoke tests.
5. **Side pose left/right** — default: two categories ("Side Arm Down Flex – Left/Right"), each with its own reference. Alternative: one category, auto-flipped to one canonical side.
6. **Node** — may I install Node 22 LTS via `winget` (system change)? Default if no: stay on Node 21 with Vite 6.
7. **Canvas** — default 1080×1350 (4:5). Alternative 1080×1920 (9:16) if your photos are full-height phone shots.
8. **Units and metrics** — default lb; weight built-in; body fat %, waist, arm, chest offered as one-click presets. Anything else from day one?
