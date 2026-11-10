# PhysiqueLapse — progress notes

Newest first. Each entry says what was done, what was verified, and what is next, so a new session can resume.

## 2026-11-10 — Example-based pose matching, uploads
- Classifier replaced: a photo goes to the pose whose examples its joints and arm angles are closest to (`PoseMatcher`, weighted pose distance: angles /30°, arm positions /0.3 torso lengths, facing /0.4; mean of the 2 nearest examples per pose; confidence d2/(d1+d2); further than 4.0 from every pose → Other). Left/right pairs share examples, mirrored. Leave-one-out on the 112 hand labels: 82 right vs 79 for the old kNN.
- Examples now come from two places: hand-set library photos (as before) and images uploaded per pose on Settings → Pose examples (`pose_examples` table, files in data/examples/, landmarks run on upload, "no person found" rejected). `POST /poses/{id}/examples`, `DELETE /poses/examples/{id}`, thumb/image routes; deleting a category drops its uploads.
- Reclassify all run on the real photos: 1017 photos, 186 changed, 451 flagged for review (the review count is high because several of the user's examples overlap, e.g. all Front Relaxed examples are phone-in-hand selfies). By eye: Front Double Bicep now only real double biceps (17), Side Arm Down Flex - Right clean (13). 134 tests pass.

## 2026-11-10 — Pose examples, incremental classify
- Classify poses now only handles new photos (never classified, flipped, or from a deleted category) and borderline ones (needs review); confident automatic poses stay. Settings → "Reclassify all" redoes every photo not set by hand (`force`).
- Settings → "Pose examples": every hand-set photo per pose (the kNN's training set), with "needs N more" below 3, and examples the other examples place in another pose flagged orange (leave-one-out); hover → remove (back to automatic). `GET /poses/examples`.
- Side mirror selfies: arm down vs. arm bent is decided by the shown arm's elbow angle (< 132° = Arm 90). Leave-one-out on the 111 examples: no change (38 vs 39 of 50 side selfies, the rule disagrees with some examples); on the 27 unlabelled photos where it changes the answer, by eye about 22 are better (arms hanging down that were in Arm 90).
- Found while checking: the examples are inconsistent (e.g. all Front Relaxed examples are mirror selfies, Front Lat Spread has mirror-selfie flexes), which is the main cause of mixed groups. Tried and rejected (no gain on leave-one-out): CLIP image kNN, CLIP+pose mix, rule blend, class-balanced kNN, mirror augmentation, self-training. 134 tests pass.

## 2026-11-10 — Face group
- New Face group across all poses (schema v7; DB backed up to the session scratchpad as physiquelapse.before-v7.db): face close-ups aligned by eye-midpoint and mouth-midpoint (rigid move, 720×720), listed with the poses on the Dashboard, Viewer and Library; export with `group: "face"`; drawer "Face visible" switch with reset; runs after Classify poses.
- Visible check: CLIP on the face centre and on the mouth/chin of the aligned close-up, lower score wins, threshold 0.85 (phone in front of the face is the main reason to leave a photo out). Prototype on the real photos: alignment consistent by eye; covered faces mostly removed, some faces with the phone over the mouth still pass. 134 tests pass.

## 2026-11-10 — Hide sleeves, faster pose review
- Sleeves check (schema v6; DB backed up to the session scratchpad as physiquelapse.before-v6.db): CLIP on the shown arm's upper arm (upright, band-masked) and on the whole person (upright), lower score wins, threshold 0.5. Runs after Classify poses. On the 1,125 real photos: 28 flagged; on a hand-checked set of 32 sleeved photos it caught 27 (misses: tilted white-tee shots, a lifted tee, one green tee) and flagged 1 cut-off sleeveless shirt.
- Viewer "Hide sleeves" button (remembered per browser, also applies to Export); Library "hide sleeves / only sleeves" filter; drawer "Sleeves cover the arms" switch with reset.
- Pose review: Accept button in the drawer (and A key), ✓ on cards needing review, "Accept poses" in the bulk bar, and a "Pose examples" strip above the needs-review list (click an example to move the selected photo there). The drawer moves on to the next photo after an accept.
- Fixed: abs (and sleeves) checks crashed when a photo was deleted while they ran. 132 tests pass.

## 2026-11-10 — Abs listed with the poses
- Abs is now a row in the Dashboard pose table and an entry in the Viewer and Library pose lists. The separate "Find abs" / "Abs" buttons and the Library "abs visible" checkbox are gone.
- Abs detection runs automatically after "Classify poses" (only unchecked photos; skipped if the CLIP weights are missing). 128 tests pass; screenshots of the Dashboard and Viewer checked.

## 2026-11-09 — Session 2 (cont.): Abs group
- New Abs group across all poses: CLIP torso check + manual override, aligned torso close-ups, Viewer/Export/Library/drawer/Dashboard support. Schema v5 (DB backed up to the session scratchpad as physiquelapse.before-v5.db).
- Verified on the real photos: 13 of 119 found, all correct by eye, clean score gap (≥ 0.80 vs ≤ 0.18); close-ups line up; Viewer screenshot checked. 127 tests pass.

## 2026-11-09 — Session 2 (cont.): drawer fixes from user screenshot
- Photo drawer image now sits in a fixed-height box (tall photos used to overflow and cover the date controls).
- Date changes are two-step: "change" → pick date → "Change date" → confirm panel showing old → new → Confirm. Removing a date and bulk "set date" / "shift days" also ask for confirmation. Low-confidence (file-time) dates get an explanation.
- Finished jobs leave the job panel after 15 s (failed ones stay until dismissed). `/library?photo=<id>` opens a photo's drawer directly.
- Verified with headless Edge screenshots of the drawer for the tall photo (IMG_4831); the confirm flow itself was not clicked through (no browser automation here).

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
