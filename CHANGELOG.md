# PhysiqueLapse — progress notes

Newest first. Each entry says what was done, what was verified, and what is next, so a new session can resume.

## 2026-11-23 — Public address (own tunnel) and GitHub Pages landing page
- **U4**: `docker-compose.yml` gains a `tunnel` service (`cloudflare/cloudflared:2026.10.7`, profile `public`, so it only starts with `docker compose --profile public up -d`). It's PhysiqueLapse's own Cloudflare Tunnel with its own token (`CLOUDFLARE_TUNNEL_TOKEN` in `.env`), reaching the app over the project's private network (`http://physiquelapse:8420`): no host port, read-only, no capabilities, 0.5 CPU / 256 MB. Better Lift's `cloudflared` service and tunnel are not involved. `docs/public-setup.md`: domain on Cloudflare, creating the tunnel and public hostname in Zero Trust, `.env`, checks (incl. Better Lift), recommended Cloudflare settings, Resend SMTP for reset emails, turning it off.
- **U5**: `site/index.html`, a static landing page (light/dark, phone-first; no screenshots of real photos, no app code or data) with "Open the app" (filled from the repository variable `APP_URL` at publish time) and the Windows download; `.github/workflows/pages.yml` publishes it with GitHub Pages (Source: GitHub Actions). Pages for a private repository needs a paid GitHub plan.
- Verified: compose config valid with and without the `public` profile; landing page checked at phone width in light and dark (no horizontal overflow). The tunnel itself waits for a domain and token.

## 2026-11-23 — Accounts: one library per person, invite sign-up, password resets
- Plan for opening the server to other people (invite-only), approved: U1 one library per account, U2 accounts, U3 email resets, U4 PhysiqueLapse's own Cloudflare Tunnel (separate from Better Lift's), U5 a GitHub Pages landing page.
- **U1 libraries** (`tenancy.py`): a library = the same folder + SQLite layout as before. The current library is a context variable set per request (from the signed-in account) and per job (queue entries carry it); `config.ORIGINALS_DIR`, `THUMBS_DIR`, `DB_PATH`, ... are looked up for it on every use, `engine()` keeps one engine per library and migrates it on first use, the job queue is round-robin across libraries (one big import can't starve someone else). The desktop app's library is DATA_DIR as before; on the server there is deliberately no default library, so nothing can touch library data outside a signed-in request or that account's jobs. Models are shared.
- **U2 accounts** (`accounts.py`, own `accounts.db`): users (argon2), sessions, invite codes (shown once, stored hashed, uses + expiry, revocable, used up atomically), per-account storage quota (default `PL_QUOTA_MB=5120`), account deletion removes the whole library. API: `/auth/status|login|logout|signup|forgot|reset|password|delete-account|usage`, admin `/admin/invites`, `/admin/users` (disable, quota, reset link, delete). Sign-in is by email; rate limits per address (Cloudflare's `CF-Connecting-IP` when present) and per email; API responses `private, no-store`. CLI `python -m app.accounts create-admin | set-password | invite | migrate-library | list`; `migrate-library` moves the single pre-accounts library into an account after backing up its database. The M1 single-password login is gone.
- **U3 password reset by email** (`mailer.py`, standard-library SMTP; `SMTP_*`, `MAIL_FROM`, `PL_PUBLIC_URL` in `.env`): same answer whether or not the email exists, 1-hour single-use links stored hashed, resetting signs out every other session. Without SMTP the admin gets a reset link to pass on.
- Frontend: sign-in (email), sign-up (`/signup?invite=…`), forgot / reset, Account (storage, password, delete account), Admin (invites with copy/share link, accounts), Privacy page; Account and Admin in the menu.
- Verified: backend suite in desktop and server mode (new: every API route needs a session; sign-up with valid / wrong / used / expired / revoked invites; **a second account can't reach the first one's photos, thumbnails, originals, series, sequences or jobs**; disable → signed out at once; delete → library folder gone; reset flow incl. single use and other sessions ended; change password; quota refusal; CLI create-admin / invite / migrate-library; fair queue; jobs run in the library that queued them). In Chrome with phone emulation against a server-mode instance: sign in, create invite, sign up from the link on another "phone", empty library, admin routes refused, used invite refused, admin sees the new account.

## 2026-11-23 — Server mode, milestone 7: live pose matching in the browser
- Capture page → **Match pose**: MediaPipe Pose (lite) runs in the browser on the camera preview and draws your skeleton over it, each joint green / amber / red by how close it is to the ghost's, with a "Match N%" badge. **Auto-capture** (per pose / series) shoots when the match stays at ≥ 85% for 1 s (the self-timer still applies).
- Matching compares shoulders, elbows, wrists and hips where they are in the camera frame (no fitting: lining up includes distance and position), in the ghost's torso lengths, after moving the ghost's stored landmarks through the same centre crop as the ghost image. Joints at the frame edge (body cut off) aren't compared; a 0.04-torso noise floor absorbs the lite (browser) vs heavy (server) model difference; the score is smoothed over ~0.5 s.
- IMAGE mode (a fresh detection per frame, as on the server): VIDEO mode's tracking drifted from 100% to ~70% on a still frame because its landmark projection assumes a square region on non-square camera frames.
- Nothing from Google at runtime: `@mediapipe/tasks-vision` pinned to 0.10.21 (same line as the backend pin; its JS and WebAssembly scanned for telemetry endpoints: none, only documentation links in error strings); the WebAssembly is copied into the build at `/mediapipe/` by a small Vite plugin (also served in dev) and the lite model is downloaded once with the other models and served behind the login at `/api/capture/pose-model`. The CSP's `connect-src 'self'` would block anything else anyway. `.wasm` served as `application/wasm`.
- Verified end to end in Chrome with a fake camera playing the pose's own reference photo: model and WebAssembly loaded from this server, Match 100%, auto-capture fired ~1.5 s after switching it on, the photo uploaded; no request to any other host. Model download list test updated.

## 2026-11-23 — Server mode, milestone 6: daily series + flipbook
- Daily series (Capture page → "New daily series"): a series of its own, not tied to a pose. The ghost is its latest photo by default (toggle Last / Reference / First); the card shows "✓ today" / "not yet today", the streak (days in a row, counting yesterday's run until today's photo is taken) and a Play button; the camera screen shows the streak and the last 7 days as a filmstrip.
- Own alignment: `series_alignments` table (schema v12) and files in `derived/series/<id>/`, so a photo can be aligned in a pose and in a series at once. `align.py` now aligns for a `PoseCategory` or a `CaptureSeries` through the same code (same alignment fields on both; the reference is picked once from the series' photos and kept; facing normalisation is never applied for a series because a flip is stored on the photo). Job `align_series`; a daily capture chains landmarks → align_series (and align_photos when the series is linked to a pose). `POST /capture/series/{id}/align`.
- Flipbook: `GET /capture/series/{id}/sequence` returns the Viewer's frame format, so the Viewer plays a series as `pose_id=series:<id>` (listed as "Daily: …" in its picker; `&autoplay=1` starts playback), and Export takes `group: "series", series_id` (same renderer, captions, speeds).
- Deleting a photo removes its series frames; the outbox announces finished uploads so the streak / last photo refresh at once.
- Verified: 176 backend tests in both modes (3 new: streak incl. gap, two shots in a day and "today isn't over yet", "last" ghost = newest; series alignment with fixture landmarks → own files not the pose's, sequence days, GIF export, delete cleanup; export validation). End to end on a copy of the real library with the real pose model: 3 photos captured into a daily series over 3 days → landmarks found, the series picked its own reference and aligned all 3, streak 3 with today ✓; screenshots of the card, camera filmstrip and flipbook.

## 2026-11-23 — Server mode, milestone 5: Capture page (reference mode)
- New **Capture** page (big centre button in the phone tab bar, sidebar entry). Pick a pose → full-screen camera with a ghost of the pose's reference over the live picture.
- Ghost framing: the ghost is centre-cropped to the video's frame shape and drawn on a canvas with the same `object-fit: cover` as the video, so ghost and live picture map pixel for pixel; mirror flips both together. Styles: faded photo, outline (white Canny edges on transparent PNG, made on the server and cached in `derived/ghost_edges`) or pose skeleton (from the ghost's stored landmarks). Ghost source: reference / last / first. Opacity, style, source, mirror, camera and timer are remembered per pose (per series) on the server. A hint appears when the phone's orientation doesn't match the ghost's. Screen kept awake; portrait lock where allowed.
- Capture: `ImageCapture.takePhoto()` where available (full sensor resolution), else the video frame at full stream resolution. A small EXIF writer (`lib/exif.ts`) adds DateTime/DateTimeOriginal/Digitized, OffsetTime*, Orientation=1, Make/Model so the photo sorts correctly in the camera roll and the server dates it. Verified by round-trip through Pillow and the app's EXIF reader.
- Review: Retake / Keep / hold to flick to the ghost. **Keep** opens the share sheet inside the tap (Save Image / save to Photos), or downloads `PhysiqueLapse_<pose>_<YYYY-MM-DD_HHMMSS>.jpg` where files can't be shared; setting "Save to camera roll after each capture" (on by default, per device). "Save today's photos to camera roll" shares all of today's captures at once.
- Outbox (`lib/outbox.ts`): every kept photo goes into IndexedDB (and memory) first, uploads in the background, and is only removed once uploaded AND saved or dismissed; retries on load, back online, back in the foreground, and every 30 s. A cancelled share never blocks or undoes the upload.
- Backend: `capture_series` table and `photos.capture_series_id / ghost_photo_id / capture` (schema v11), `services/capture.py` (`pick_ghost`, `series_for_pose`, edges, `ingest_capture`), `api/capture.py` (`GET /capture/series`, `POST /capture/series/for-pose/{id}`, `POST /capture/series`, `GET|PATCH /capture/series/{id}`, `GET /capture/series/{id}/ghost`, `GET /capture/ghost/{photo}/edges`, `POST /capture/upload`). A capture is dated by the phone's clock (converted to the server's local time, `taken_at_source=capture`, high confidence), assigned to the pose (`pose_source=capture`: never reclassified, not used as a kNN example), never skipped as a near-duplicate, and gets landmarks → align without classification. The same bytes uploaded twice return the same photo (safe retries).
- `docs/mobile-testing.md`: checklist for iOS Safari (tab + home screen) and Android Chrome, with known quirks.
- Verified: 173 backend tests in both modes (6 new: ghost selection incl. excluded photos and daily series, phone time → server time, capture upload + retry + no classification + no near-duplicate skip, validation, series settings + outline PNG). End to end in Chrome with a fake camera against a copy of the real library (v8 → v11 upgrade): camera opens, ghost drawn in all three styles, photo taken at 3840×2160, kept, uploaded, dated, assigned, landmarks + alignment ran, outbox emptied. Real phones not tried yet.

## 2026-11-23 — Server mode, milestone 4: phone layout + installable app
- Phone layout (below 768 px): bottom tab bar (Home, Library, Upload, Viewer, More), "More" sheet with every page and Log out, safe-area insets (notch / home bar), job panel full-width above the tab bar, page headers wrap. Desktop layout unchanged.
- Library: filters fold behind a Filters button, 3-column grid, photo drawer opens full screen; touch: long-press selects a photo, and while anything is selected a tap adds/removes instead of opening.
- Viewer: swipe left/right on the photo steps through frames, date/day line above the transport, side panel (modes, playback, measurements) behind a button; Alignment editor panel stacks under the photo.
- Fixed a CSS precedence bug: `.btn`, `.input`, `.select`, `.card` etc. were unlayered, so utility classes on the same element lost (e.g. `pl-7` on the Library search box, whose icon overlapped the text). They now live in `@layer components`.
- Installable app (PWA): `manifest.webmanifest` (standalone, portrait), icons (192/512, maskable, apple-touch), iOS meta tags, a service worker that caches nothing (registered only over HTTPS, so never in the desktop app). The server sends the manifest with its MIME type and `sw.js` with `no-cache`.
- Docker: `MPLCONFIGDIR=/tmp/matplotlib` (MediaPipe imports matplotlib, which complained it couldn't write its cache).
- Caching: thumbnails/previews were already immutable with ETag; aligned frames are versioned (`?v=`), so nothing else was needed.
- Verified with phone-emulation screenshots (390×844, touch) of every page on the real library, and desktop screenshots (1440×900) for regressions; no page overflows horizontally. Not yet tried on a real phone.

## 2026-11-23 — Server mode, milestone 2: browser upload + auto-processing
- Resumable chunked uploads (`POST /imports/upload-session/{id}/files/init`, `PUT …/files/{fileId}?offset=`, `GET …/files/{fileId}`, `GET /imports/upload-session/{id}`): 8 MB chunks, the offset must match what the server has (a repeated chunk gets 409 with the right offset), the same file announced again resumes, bytes collect in `incoming/<session>/.parts/` and move into the staging tree when complete, then the existing import job runs. Type allow-list (images only) and a per-file cap (`PL_MAX_UPLOAD_MB`, default 300). Schema v10 (`upload_items`).
- Frontend uploader now always uses it: 2 files at a time, up to 8 retries per chunk with backoff, asks the server how far it got after an error, and reuses an interrupted (still open) session after a reload. Camera-roll picker on phones; partial failures are reported without losing the files that made it.
- Auto-processing: setting `auto_process_uploads` (unset = on in server mode, off in the desktop app). After an upload the new photos only go landmarks → classify → align, through a generic job chain (`chain` param / `JobRunner.enqueue_chain`); the job drawer shows each step.
- HEIC: `GET /photos/{id}/original` serves a full-resolution upright JPEG (EXIF dates kept, cached in `derived/fullres`) for HEIC/HEIF originals or with `?as_jpeg=1`; the original stays byte-identical on disk.
- Server mode: reading server folders (import by path, folder browser, Snapchat folder scan, zip by path) only inside `PL_IMPORT_ROOTS` (off by default; the Import page hides the folder box); the one-shot multipart upload refuses non-images and files over the cap.
- Verified: 167 tests pass in both modes (8 new: chunked upload + resume + duplicate chunk + import, limits, path traversal stays in staging, committed session refuses files, HEIC date + JPEG view, auto-chain runs only on the new photos in order, default per mode, path imports vs. allowed roots); frontend typechecks and builds.

## 2026-11-23 — Server mode, milestone 1: Docker + login + HTTPS docs
- Plan for the home-server version (same codebase, `PL_MODE=server`): M1 Docker + login, M2 browser upload + auto-processing, M3 benchmark + GPU, M4 mobile/PWA, M5 Capture page (reference mode), M6 daily mode + flipbook, M7 in-browser pose matching. Each milestone goes on a short `feat/*` branch off `main`.
- Server checked over SSH (read-only): Ubuntu 26.04.1, 8 CPUs, 30 GiB; the other app (Better Lift: PocketBase 127.0.0.1:8090, push 127.0.0.1:8092, cloudflared) is systemd, not Docker; Docker not installed; no `tailscale serve` config; tailnet HTTPS certificates not enabled; RTX 3060 Ti without a driver (recommended `nvidia-driver-595-open`); host clock UTC.
- `config`: `PL_MODE`, bind host/port per mode (desktop unchanged: 127.0.0.1:8000), cookie/session/origin settings, `PL_CV_WORKERS`, `PL_JOB_NICE`, `PL_THREADS` (CLIP's thread count now honours it; `os.cpu_count()` sees all host CPUs in a container). `desktop.py` always forces desktop mode.
- Login (`app/auth.py`, `api/auth.py`): argon2 hash in the new `auth_state` table (never in `settings`), random session token stored as SHA-256 in `auth_sessions` (sliding 30-day expiry), cookie HttpOnly + Secure + SameSite=Strict, failed-login lockout (5 free, then 30 s doubling to 1 h, plus a global cap), Origin check on every state-changing `/api` request, CSP/nosniff/frame headers in server mode. CLI: `python -m app.auth set-password | revoke-sessions | status`. Desktop mode: middleware passes everything through. Frontend: Login page, 401 → login redirect with a safe `next`, Log out in the sidebar.
- Schema v9 (new tables only). Any schema upgrade now first copies the database to `physiquelapse.before-v<N>.db`.
- Job runner: `cv` lane can run `PL_CV_WORKERS` threads; in server mode job threads lower their own priority. Server-mode startup queues the existing model download when models are missing (`PL_AUTO_DOWNLOAD_MODELS=0` turns it off; tests do).
- `Dockerfile` (node build stage → python:3.12-slim, CPU torch, non-root, tini, healthcheck), `docker/entrypoint.sh`, `docker-compose.yml` (project `physiquelapse`, own network, `127.0.0.1:8420`, bind-mounted data, `cpus: 5`, `mem_limit: 8g`, no-new-privileges, cap_drop ALL), `.env.example`, `.dockerignore`, `.gitattributes` (LF for shell scripts).
- `docs/server-setup.md` (port checks, Docker install, configure, move the existing library, start, `tailscale serve --https=8443`, update, backup/restore, troubleshooting; each step re-checks Better Lift) and `docs/gpu-setup.md` (driver, reboot, Container Toolkit, `docker run --gpus all … nvidia-smi`, re-checks, undo). README: "Run on a home server" + privacy notes.
- CI (`.github/workflows/ci.yml`): backend ruff + pytest in desktop and server mode, frontend typecheck + build, Docker image build + smoke test (health 200, API 401, set-password).
- Verified: 159 backend tests pass with `PL_MODE` unset and with `PL_MODE=server` (16 new: every API route found in the OpenAPI schema returns 401 without a session, media routes and docs, login/logout, cookie flags, lockout, forged cookie, cross-origin refusal incl. X-Forwarded-Host, hash never in settings, new password logs out everyone, CLI, v8→v9 upgrade with backup, desktop mode has no login); ruff clean; frontend typechecks and builds. **Not yet verified**: the Docker image build (Docker Desktop isn't running on this PC and the server has no Docker yet; CI will build it), and the deploy itself.
- Next: you install Docker + enable tailnet HTTPS (docs/server-setup.md steps 2 and 6), then I deploy over SSH and check; then M2 (browser upload + auto-processing).

## 2026-11-10 — Windows desktop app
- `app/desktop.py`: the app in its own window (pywebview on WebView2), server on a free 127.0.0.1 port, "Starting…" screen, first-launch model download (job `download_models`, `GET/POST /api/system/models[/download]`, list in `services/model_files.py`, shared with `scripts/download_models.py`; CLIP now fetched as one file from LAION's Hugging Face repo, same 605,143,316 bytes), empty library opens on Start here, second launch opens a window on the running instance, closing the window stops the server, logs in `<data>/logs`.
- Frozen builds keep data in `%LOCALAPPDATA%\PhysiqueLapse` and serve the bundled frontend (`config.FROZEN`).
- `packaging/`: PyInstaller spec (one folder, no console, icon, version info), `build.ps1` (frontend → PyInstaller → portable zip → Inno Setup installer if installed), `installer.iss` (per-user, no admin, Start menu / desktop shortcuts, uninstall asks before deleting the library). `.github/workflows/release.yml`: tests + build + release on `v*` tags. README "Download" section. `desktop` extra (pywebview, pyinstaller).
- Verified: from source the window opens on Start here and closing it shuts down cleanly; the frozen exe starts in ~2 s and downloaded a missing model by itself. The first frozen build failed landmark extraction (mediapipe imports matplotlib, which the spec excluded); fixed. Build size: 1,174 MB folder, 446 MB zip. 143 tests pass.

## 2026-11-10 — Start here guide
- New "Start here" page (`/guide`, first sidebar item; linked from the Dashboard header and its empty state): intro and privacy note, a 6-step quick-start checklist that ticks itself from the real library (photos imported, landmarks done, pose examples, nothing needing review, aligned), a card per page and per built-in feature with a button to open it, tips for common problems, and keyboard shortcuts (each checked against the code). Checked by screenshot (5 of 6 steps done on the user's library).

## 2026-11-10 — Filter people
- New page Filter people (sidebar, Library header button, Dashboard "Not you" tile): scan finds the subject's face in each photo (OpenCV YuNet detector + SFace recognizer, models in data/models via download_models.py, ~38 MB, Apache-2.0; no new packages), step 1 asks which of the most common faces are you, then tabs Doesn't look like you / Not you / Face hidden / You / Your examples with select, That's me, Not me, Back to automatic, Delete selected and Delete all not-you. Scan also runs after Classify poses. Schema v8 (photos.person_label / person_status / person_score, table person_faces; DB backed up to the scratchpad as physiquelapse.before-v8.db).
- Tested on the real photos before building: CLIP can't tell people apart (its outliers were the user in other clothes). SFace on 1,055 usable faces: with 10 examples, 142 faces scored < 0.25 and by eye nearly all were the user (cap over the eyes, looking down at the phone, steep angles); 200 varied examples brought that to 35. So a low score is never "not you" on its own: "Not you" = photos the user marked plus faces matching someone they marked; everything uncertain is listed for review. Best-match scoring (top 1), threshold 0.40.
- Scan of the real library: 1,122 photos, 1,055 usable faces, 67 face hidden. Step 1 screen checked by screenshot; steps after it not clicked through (no labels set on the user's photos). 141 tests pass.

## 2026-11-10 — Export frame rate
- Export window → Frame → Frame rate: auto (as before: 30 for MP4 / 15 for GIF, raised up to 60 / 50 when the photo speed needs it) or a fixed 24/25/30/50/60 (MP4) or 10–50 (GIF). Warns when photos per second exceed frames per second (some photos would get no frame). Length is unchanged by the frame rate. `ExportIn.frame_rate`; 139 tests pass.
- Switched the working copy back to `main` (the `githubpages` branch pointed at the same commit; left in place).

## 2026-11-10 — Poses stay as the user left them; New pose; export preview
- Fixed: every server start re-created deleted built-in poses and reset their order. Built-ins are now offered once (setting `builtin_categories_offered`; existing libraries count all current built-ins as offered); later versions can still add new built-ins once. Deletes, renames, order and user poses survive restarts (test). The re-created Back Double Bicep was deleted again (DB copy before: scratchpad physiquelapse.before-offered.db).
- New pose: a "New pose" card at the end of the Dashboard grid and a box in Settings → Pose examples; both jump to the new pose's examples (`/settings#pose-<id>`). Empty pose cards link there too.
- Export window: live preview that plays the exact photos, order, timing (holds, speed, crossfade, boomerang), shape, captions (real dates / day numbers) and progress bar, with play / pause / restart / scrub; finished exports play inline (▶). `/viewer?pose_id=<id>&export=1` opens the window. Checked by screenshot; caught and fixed an off-by-a-month day number in the preview.
- 138 tests pass.

## 2026-11-10 — Export settings
- Viewer → Export now has: speed 0.05–60 photos/s (slider, presets, or "total length" which works out the speed); hold first / last photo; crossfade; boomerang; newest first; one photo per day / week / month and every Nth; shape original / 1:1 / 4:5 / 9:16 / 16:9 with fill (crop) or fit (bars, colour picker); any width; MP4 quality; caption title, top/bottom, text size; progress bar. Live estimate of photo count, length and size, a preview of shape and caption, settings remembered per browser, reset button.
- Server: `ExportIn` fields above; `pick_photos`, `play_order`, `compose`, `output_size`, `draw_progress`; `frame_schedule` takes holds (default end hold unchanged: two photos' time); output frame rate rises up to 60 (MP4) / 50 (GIF) when the speed needs it.
- Verified: 137 tests pass (3 new); a real 9:16 export of Front Double Bicep with every option on checked frame by frame (9.3 s as estimated). The dialog itself was not clicked through (no browser automation here).

## 2026-11-10 — Pose cards on the Dashboard
- Dashboard pose table replaced by a grid of cards: picture (reference photo if still in the pose, else the latest photo; latest close-up for Abs / Face), name (wraps to 2 lines), count, View + Align. Empty poses show a placeholder.
- Library opened on one pose: the pose name is the page title, with the same View + Align buttons at the top (`PoseActions`, shared). Abs / Face get View only (their close-ups are aligned when found).
- Settings → Pose examples list: 9 s → 0.4 s (leave-one-out check now in memory), and ✕ hides the example at once. 134 tests pass; Dashboard and Library screenshots checked.

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
