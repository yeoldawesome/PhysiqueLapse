# PhysiqueLapse — progress notes

Newest first. Each entry says what was done, what was verified, and what is next, so a new session can resume.

## 2026-11-09 — Session 2 (cont.): joint alignment, pose match, left/right groups
- Alignment now fits shoulders, elbows and wrists too (default "joints"); elbow/wrist landing error dropped 1.3–4× on the real photos. No image warping (it would fake size changes).
- Pose-match score per photo + Viewer/Export filter; mirror-selfie groups split into left/right by real side; hand-assigned photos follow their side.
- Schema v4 (DB backed up beforehand to the session scratchpad as physiquelapse.before-v4.db). 125 tests pass; frontend builds; aligned frames checked by eye with reference joints overlaid.
- Next: user review; photo #116 (group shot) is still classified as a double bicep and should be moved to Other.

## 2026-11-09 — Session 2 (cont.): body sizes from photos
- New: body-outline masks + size estimates (shoulders, lats, waist, V-taper, upper arm, forearm) relative to torso length, per pose type, with trend lines and optional inch calibration from tape measurements. Stats → From photos; drawer "sizes" overlay; Viewer strip.
- Verified on the 119 real photos: same-day noise 1–3 % for the main sizes in their preferred poses (table in PLAN.md); overlays checked by eye; 122 tests pass; ruff clean; frontend builds.
- Fixed while verifying: numpy bool not JSON-serializable (rows silently missing), forearm measured on the phone hand / merged with the hip, shoulder width accepted with an arm raised along the shoulder line.
- Next: user review in the browser; log a few tape measurements (Arm, Waist) to switch the charts to inches.

## 2026-11-09 — Session 2 (cont.): Snapchat zip upload + physique filter
- Import page accepts the Snapchat export zips (upload or by path); job unpacks photos only and runs the physique filter; Resume after failure; "Free space" cleanup.
- Fixed CLIP loading on deep paths (flat weights file) and imports stuck in "running" after a failure or restart.
- Verified: 113 tests pass; end-to-end with real CLIP + pose models on a scratch data dir (19/19 lifting photos imported, 10/10 synthetic non-photos rejected).
- Next: run on the user's real export, calibrate thresholds from the score histogram.

## 2026-11-09 — Session 2 (cont.): finer categories, better alignment
- Added Front Lat Spread, split side selfies into Arm Down / Arm 90; stage poses excluded when a phone is in hand; features v2 (elbow-to-midline).
- New body-fit alignment (weighted fit over head + shoulders + hips), best-fit default reference, rotation clamp 15°. Residuals on the real photos dropped 2–3× (table in PLAN.md "Second pass"). Settings page has an "Align by" column per category.
- Verified: 111 tests pass, ruff clean, frontend builds, aligned contact sheets checked by eye. App re-aligned and running at http://127.0.0.1:8000.
- Next: user review in the browser; Snapchat export (Q3).

## 2026-11-09 — Session 2 (cont.): first run on real photos
- Imported the user's 122 iCloud lifting photos (videos skipped) into `data/`; ran landmarks, classify, align. Details and numbers in PLAN.md (real-data run).
- Added mirror-selfie categories + phone-hand rule; tightened double-bicep rule; default reference is now the category medoid. CLIP + pose model smoke tests pass (`pytest -m models`). 103 tests pass.
- App left running at http://127.0.0.1:8000.
- Next: user review in the browser (fix the few misclassified photos, star preferred references), Snapchat export (Q3).

## 2026-11-09 — Session 2 (cont.): M4 viewer/export, M5 stats, M6 Snapchat, README
- Added export job (MP4/GIF, captions, crossfade), Viewer page, Stats page + CSV import, Snapchat scanner/import job/review queue, CLIP wrapper (offline at runtime), README, schema migration v2.
- Verified: 100 pytest tests pass via `.\start.ps1 -Test`; ruff clean; frontend type-checks and builds; MP4 caption checked on an extracted frame.
- Fixed: export frame-timing drift at fps that don't divide 30; detached session in the export job; closed-image bug in Snapchat scoring; delete overwriting the ledger source path.
- Not verified: browser click-through; Snapchat parser on a real export (Q3); pose/alignment/filter quality on real photos (Q4); CLIP smoke test (weights still downloading).
- Next: answers to PLAN §11 Q3/Q4, then real-data verification and threshold calibration.

## 2026-11-09 — Session 2: M1 skeleton, M2 pose, M3 alignment
- Built backend (FastAPI + SQLModel + JobRunner), ingest/dates/dedupe, pose backend + landmarks + features + rules/kNN, alignment + editor API + eval script; frontend shell, Dashboard, Library (filters, bulk bar, drawer), Import (drop/folder/path picker/history), Settings, Alignment editor.
- Verified: 79 pytest tests pass (`.\start.ps1 -Test`); `npm run build` passes; live server imported the synthetic sample set and ran real landmark/classify/align jobs with the downloaded heavy model.
- Fixed during verification: SQLModel naive-datetime rejection, `date` field/type clash, classify job deadlock (progress write while holding the write lock), near-duplicate copies misreported as "previously removed", non-deterministic folder walk order.
- Not verified: UI clicked through in a browser; pose rules and alignment on real photos (needs sample photos, PLAN §11 Q4).
- Next: M4 viewer + MP4/GIF export, then M5 stats.

## 2026-11-09 — Session 1: environment check and plan (no code yet)
- Checked the machine: Windows 11, Python 3.12.2, Node 21.6.2 (EOL; Vite 7 incompatible, Vite 6 OK), git, no ffmpeg, RTX 3050 Ti (CPU-only plan).
- MediaPipe: current 1.2.2 embeds Clearcut telemetry to play.googleapis.com/log with no opt-out; 0.10.21 (last Windows/py3.12 wheel before it) verified clean by binary search. Decision: pin 0.10.21 behind a `PoseBackend` interface.
- Pose model URLs verified (heavy 30.7 MB). Snapchat export not found on disk; path requested.
- Wrote PLAN.md (architecture, schema, API, milestones, risks, 8 open questions).
- Next: answers to PLAN.md §11, then M1 (skeleton).
