# PhysiqueLapse

A local-only physique progress tracker. Import progress photos, date them, sort them by pose, align every photo of a pose onto a reference, then scrub or export the time-lapse next to your body measurements. A Snapchat Memories importer filters a whole export down to physique photos.

Everything runs on this computer. The server binds to `127.0.0.1` only. The only network traffic is installing dependencies and the one-time model download. It can also run on your own home server and be used from your phone (see [Run on a home server](#run-on-a-home-server)).

## Download (Windows app)

Get **`PhysiqueLapse-<version>-Setup.exe`** from the [Releases page](../../releases/latest), run it, and open PhysiqueLapse from the Start menu. It runs in its own window; no Python, Node or browser setup needed.

- Windows 10 or 11, 64-bit. No administrator rights needed.
- First launch downloads the models (about 650 MB, one time); progress shows in the job panel.
- Windows may say **"Windows protected your PC"** because the app isn't code-signed: click **More info → Run anyway**.
- Your library lives in `%LOCALAPPDATA%\PhysiqueLapse` (photos, database, models, logs). Updating or reinstalling keeps it; the uninstaller asks before deleting it.
- Prefer no installer? Download **`PhysiqueLapse-<version>-portable.zip`**, unzip anywhere and run `PhysiqueLapse.exe`.
- New here? The app opens on a **Start here** page that walks through everything.

### Building the app yourself

```powershell
.\start.ps1 -Setup                                   # once: .venv, dependencies, models
.venv\Scripts\python -m pip install -e "backend[desktop]"
.\packaging\build.ps1 -Version 0.2.0                   # -> packaging\out\ (portable zip; installer too if Inno Setup 6 is installed)
```

Releases are built by GitHub Actions (`.github/workflows/release.yml`): push a tag such as `v0.2.0` and the installer and zip are attached to a new release. Run the app from source in its window with `cd backend; ..\.venv\Scripts\python -m app.desktop`.

## Quick start from source (Windows)

```powershell
.\start.ps1            # first run: creates .venv, installs everything, downloads models, builds the UI, opens http://127.0.0.1:8000
.\start.ps1 -Dev       # API with auto-reload on :8000 + Vite hot reload on :5173
.\start.ps1 -Test      # backend test suite
.\start.ps1 -Setup     # only install / update dependencies and models
```

macOS / Linux: `./start.sh`, `./start.sh --dev`, `./start.sh --test` (or `make dev`, `make test`, `make eval`).

Requirements: Python 3.12, Node 20.19+ or 22 LTS (Node 21 works with the pinned Vite 6). No system ffmpeg is needed; a static ffmpeg ships with `imageio-ffmpeg`.

## Run on a home server

The same app runs on a home server in Docker (`PL_MODE=server`), so you can use it from your phone and laptop in a browser while the processing runs on the server. Differences from the desktop app:

- **Accounts**: people join with an invite code you create on the Admin page, sign in with email + password (argon2-hashed, HTTP-only secure session cookie, rate-limited), and reset forgotten passwords by email. **Each account has its own library** (own folder and database), so nobody can see anyone else's photos; per-account storage limits. Every `/api` route needs a session, photos and thumbnails included.
- **Reachable only on your tailnet**: the container publishes its port on the host's `127.0.0.1` only, and `tailscale serve` adds HTTPS with an automatic `*.ts.net` certificate. Phone cameras need HTTPS. No router ports are opened.
- **Shares the machine politely**: CPU and memory caps in `docker-compose.yml`, configurable job concurrency, and job threads run at lower priority. All of these are set in `.env`.
- Models download into the data volume on first start, not into the image.

```bash
cp .env.example .env && nano .env
docker compose up -d --build
docker compose exec physiquelapse python -m app.accounts create-admin --email you@example.com
tailscale serve --bg --https=8443 http://127.0.0.1:8420
```

Full steps, with checks that other apps on the server keep working, plus updating and backups: [docs/server-setup.md](docs/server-setup.md). GPU setup: [docs/gpu-setup.md](docs/gpu-setup.md).

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
- The Capture page's live pose matching runs MediaPipe in your browser from files this app serves (`/mediapipe/`, the lite model behind the login); the browser fetches nothing from Google.
- The app makes no network calls at runtime besides the one-time model download (desktop and server).
- Network exposure is opt-in: only server mode (`PL_MODE=server`) listens beyond this computer, and only behind a login. The Docker setup publishes it on the server's loopback for `tailscale serve`, so it's reachable only from your tailnet. In server mode, a Content-Security-Policy (`connect-src 'self'`) also stops the web page from contacting anything but your server.

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
