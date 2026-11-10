<#
.SYNOPSIS
  PhysiqueLapse launcher for Windows.

.DESCRIPTION
  First run: creates .venv, installs backend + frontend dependencies, downloads models.
  Every run: builds the frontend if sources changed, starts the server on http://127.0.0.1:8000
  and opens the browser.

  -Dev     uvicorn --reload on 8000 + Vite dev server on 5173 (hot reload). Opens 5173.
  -Setup   only install / update dependencies and models, then exit.
  -NoOpen  do not open the browser.
  -Test    run the backend test suite and exit.
#>
param(
  [switch]$Dev,
  [switch]$Setup,
  [switch]$NoOpen,
  [switch]$Test
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Stamp = Join-Path $Venv ".deps-stamp"
$Port = if ($env:PORT) { $env:PORT } else { "8000" }

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- python
if (-not (Test-Path $Py)) {
  Step "Creating virtual environment (.venv)"
  $sysPy = (Get-Command py -ErrorAction SilentlyContinue)
  if ($sysPy) { & py -3.12 -m venv $Venv } else { & python -m venv $Venv }
  if (-not (Test-Path $Py)) { throw "Could not create .venv. Install Python 3.12 from python.org." }
  & $Py -m pip install --upgrade pip | Out-Null
}

$pyproject = Join-Path $Backend "pyproject.toml"
$needPip = $Setup -or -not (Test-Path $Stamp) -or ((Get-Item $pyproject).LastWriteTime -gt (Get-Item $Stamp).LastWriteTime)
if ($needPip) {
  Step "Installing backend dependencies (first time: several minutes, ~1.5 GB incl. PyTorch CPU)"
  & $Py -m pip install -e "$Backend[dev]" --extra-index-url https://download.pytorch.org/whl/cpu
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
  New-Item -ItemType File -Force $Stamp | Out-Null
}

if ($Test) {
  Push-Location $Backend
  try { & $Py -m pytest @args } finally { Pop-Location }
  exit $LASTEXITCODE
}

# ---------------------------------------------------------------- models
$models = Join-Path $Root "data\models"
if ($Setup -or -not (Test-Path (Join-Path $models "pose_landmarker_heavy.task"))) {
  Step "Downloading models (one time)"
  & $Py (Join-Path $Backend "scripts\download_models.py")
  if ($LASTEXITCODE -ne 0) { Write-Warning "Model download failed. The app runs, but landmark/filter jobs will report missing models." }
}

# ---------------------------------------------------------------- frontend
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) { throw "Node.js / npm not found. Install Node 20.19+ or 22 LTS from nodejs.org." }
Push-Location $Frontend
try {
  if ($Setup -or -not (Test-Path "node_modules") -or ((Get-Item "package.json").LastWriteTime -gt (Get-Item "node_modules").LastWriteTime)) {
    Step "Installing frontend dependencies"
    npm install --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
  }
  if (-not $Dev -and -not $Setup) {
    $dist = Join-Path $Frontend "dist\index.html"
    $newest = Get-ChildItem -Recurse -File src, index.html, vite.config.ts, package.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not (Test-Path $dist) -or $newest.LastWriteTime -gt (Get-Item $dist).LastWriteTime) {
      Step "Building frontend"
      npm run build
      if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
    }
  }
} finally { Pop-Location }

if ($Setup) { Step "Setup complete. Run .\start.ps1 to start."; exit 0 }

# ---------------------------------------------------------------- run
if ($Dev) {
  Step "Dev mode: API on http://127.0.0.1:$Port, UI on http://127.0.0.1:5173"
  $api = Start-Process -FilePath $Py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $Port, "--reload", "--reload-dir", "app" -WorkingDirectory $Backend -PassThru -NoNewWindow
  try {
    if (-not $NoOpen) { Start-Process "http://127.0.0.1:5173" }
    Push-Location $Frontend
    npm run dev
  } finally {
    Pop-Location
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
  }
} else {
  Step "Starting PhysiqueLapse on http://127.0.0.1:$Port (Ctrl+C to stop)"
  if (-not $NoOpen) {
    Start-Job -ScriptBlock { Start-Sleep -Seconds 3; Start-Process "http://127.0.0.1:$using:Port" } | Out-Null
  }
  Push-Location $Backend
  try { & $Py -m uvicorn app.main:app --host 127.0.0.1 --port $Port } finally { Pop-Location }
}
