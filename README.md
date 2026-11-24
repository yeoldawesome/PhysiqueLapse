# PhysiqueLapse

A local-only physique progress tracker. Import progress photos, date them, sort them by pose, align every photo of a pose onto a reference, then scrub or export the time-lapse next to your body measurements. A Snapchat Memories importer filters a whole export down to physique photos.

Everything runs on this computer. The server binds to `127.0.0.1` only. The only network traffic is installing dependencies and the one-time model download.

## Download (Windows and Mac)

### Windows

Get **`PhysiqueLapse-<version>-Setup.exe`** from the [Releases page](../../releases/latest), run it, and open PhysiqueLapse from the Start menu. It runs in its own window; no Python, Node or browser setup needed.

- Windows 10 or 11, 64-bit. No administrator rights needed.
- Uses your GPU automatically (NVIDIA, AMD or Intel) for the image model and video export, and falls back to the CPU when there isn't one. Nothing extra to install; Settings → System shows what it's using.
- First launch downloads the pose and face models (about 75 MB, one time); progress shows in the job panel.
- Windows may say **"Windows protected your PC"** because the app isn't code-signed: click **More info → Run anyway**.
- Your library lives in `%LOCALAPPDATA%\PhysiqueLapse` (photos, database, models, logs). Updating or reinstalling keeps it; the uninstaller asks before deleting it.
- Prefer no installer? Download **`PhysiqueLapse-<version>-portable.zip`**, unzip anywhere and run `PhysiqueLapse.exe`.
- New here? The app opens on a **Start here** page that walks through everything.

### Mac (Apple Silicon: M1 or newer, macOS 12+)

Get **`PhysiqueLapse-<version>-mac.dmg`** from the [Releases page](../../releases/latest), open it and drag **PhysiqueLapse** into **Applications**.

- The app isn't notarized by Apple, so the first launch is blocked: open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to PhysiqueLapse. (Or once in Terminal: `xattr -dr com.apple.quarantine /Applications/PhysiqueLapse.app`.)
- The image model runs on the Apple GPU / Neural Engine (Core ML) and video export on the hardware encoder (VideoToolbox), with the CPU as fallback.
- Your library lives in `~/Library/Application Support/PhysiqueLapse`. Deleting the app keeps it.
- Intel Macs aren't built; run from source there (`./start.sh`).

### Building the app yourself

```powershell
.\start.ps1 -Setup                                   # once: .venv, dependencies, models
.venv\Scripts\python -m pip install -e "backend[desktop]"
.\packaging\build.ps1 -Version 0.2.0                   # -> packaging\out\ (portable zip; installer too if Inno Setup 6 is installed)
```

Mac: `python3.12 -m venv .venv && .venv/bin/pip install -e "backend[desktop]" && ./packaging/build-mac.sh 0.2.0` (-> `packaging/out/PhysiqueLapse-0.2.0-mac.dmg`).

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on Windows and macOS runners: push a tag such as `v0.2.0`, or use **Actions → Release desktop apps → Run workflow** with a version, and the Windows installer, zip and Mac disk image are attached to a new release. Run the app from source in its window with `cd backend; ..\.venv\Scripts\python -m app.desktop`.

## Quick start from source (Windows)

```powershell
.\start.ps1            # first run: creates .venv, installs everything, downloads models, builds the UI, opens http://127.0.0.1:8000
.\start.ps1 -Dev       # API with auto-reload on :8000 + Vite hot reload on :5173
.\start.ps1 -Test      # backend test suite
.\start.ps1 -Setup     # only install / update dependencies and models
```

macOS / Linux: `./start.sh`, `./start.sh --dev`, `./start.sh --test` (or `make dev`, `make test`, `make eval`).

Requirements: Python 3.12, Node 20.19+ or 22 LTS (Node 21 works with the pinned Vite 6). No system ffmpeg is needed; a static ffmpeg ships with `imageio-ffmpeg`.

## Workflow

1. **Import** photos: drag files or folders in, or point at a folder by path (faster for thousands of files). Exact and near duplicates are skipped and listed in the import's details with "keep anyway".
2. **Dates** come from EXIF, then Snapchat metadata, then the filename, then the file time. Low-confidence dates are highlighted; fix them in the photo drawer or with the bulk bar (set, shift by days, re-derive).
3. **Dashboard → Extract landmarks**, then **Classify poses**. Give each pose a few examples in **Settings → Pose examples** (upload images or set poses in the Library); every photo goes to the pose its joints and angles are closest to. **Reclassify all** redoes everything.
4. **Pick a reference** per pose (the star in the photo drawer; the earliest good photo is used by default) and **Align**. Fix any photo in the **Alignment editor**.
5. **Viewer**: scrub, play, onion skin, split view, measurements under each frame, **Export** MP4 or GIF.
6. **Stats**: add weight and measurements, or import a CSV (sample files on the page).

## Where things live

| What | Where |
|---|---|
| Everything the app stores | `data/` (set `DATA_DIR` to move it) |
| Your originals, byte-identical copies | `data/originals/` (named by content hash) |
| Thumbnails, previews, landmarks, aligned frames, exports | `data/derived/` |
| Pose model | `data/models/pose_landmarker_heavy.task` |
| CLIP weights (Snapchat filter) | `data/models/open_clip/` |
| Database | `data/physiquelapse.db` (SQLite) |
| Deleted photos | `data/trash/` (your source files are never touched) |

**Backup**: stop the app and copy the `data/` folder.

## Privacy notes

- MediaPipe is pinned to **0.10.21**. Every newer release embeds a usage-metrics uploader to Google with no opt-out (see `PLAN.md` §0).
- CLIP runs with `HF_HUB_OFFLINE=1` after the one-time download.
- The app makes no network calls at runtime besides the one-time model download.
- It only listens on `127.0.0.1`: nothing else on your network can reach it.

## Tools

```powershell
.venv\Scripts\python backend\scripts\download_models.py          # (re)download models
.venv\Scripts\python backend\scripts\eval_alignment.py --rows    # alignment residuals per pose
.venv\Scripts\python backend\scripts\make_sample_set.py out_dir   # synthetic images for testing imports
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Pose model not found" | run `download_models.py`, check `data/models/` |
| "CLIP weights not found" | run `download_models.py` (needs ~600 MB once) |
| A job says "Server restarted before this job finished" | click the retry icon in the job drawer; jobs continue where they stopped |
| HEIC files fail | `pip install -U pillow-heif` inside `.venv` |
| Port 8000 busy | set `PORT` before starting, e.g. `$env:PORT=8010; .\start.ps1` |

Design, decisions and progress: [PLAN.md](PLAN.md). Session notes: [CHANGELOG.md](CHANGELOG.md).
