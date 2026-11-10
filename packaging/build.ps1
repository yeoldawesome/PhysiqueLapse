<#
.SYNOPSIS
  Builds the PhysiqueLapse Windows app.

.DESCRIPTION
  1. builds the frontend (npm run build)
  2. freezes the app with PyInstaller -> packaging\dist\PhysiqueLapse\PhysiqueLapse.exe
  3. zips that folder -> packaging\out\PhysiqueLapse-<version>-portable.zip
  4. if Inno Setup 6 is installed, builds the installer -> packaging\out\PhysiqueLapse-<version>-Setup.exe

  Needs the .venv from start.ps1 -Setup plus the desktop extra:
    .venv\Scripts\python -m pip install -e "backend[desktop]"

.EXAMPLE
  .\packaging\build.ps1 -Version 0.2.0
#>
param(
  [string]$Version = "",
  [switch]$NoZip,
  [switch]$NoInstaller
)

# npm and PyInstaller write progress and warnings to stderr; with "Stop", Windows PowerShell 5.1
# would abort on the first such line. Failures are caught by the exit-code checks instead.
$ErrorActionPreference = "Continue"
$Pkg = $PSScriptRoot
$Root = Split-Path $Pkg -Parent
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "No .venv found. Run .\start.ps1 -Setup first." }

if (-not $Version) {
  $Version = (Select-String -Path (Join-Path $Root "backend\pyproject.toml") -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
}
$Version = $Version.TrimStart("v")
$parts = ($Version -split "[.-]")[0..2] | ForEach-Object { [int]($_ -replace "\D", "") }
while ($parts.Count -lt 3) { $parts += 0 }
$tuple = "($($parts[0]), $($parts[1]), $($parts[2]), 0)"
Write-Host "==> Building PhysiqueLapse $Version" -ForegroundColor Cyan

# version into the .exe's file properties
$vi = Join-Path $Pkg "version_info.txt"
(Get-Content $vi -Raw) -replace "filevers=\([^)]*\)", "filevers=$tuple" -replace "prodvers=\([^)]*\)", "prodvers=$tuple" `
  -replace 'StringStruct\("FileVersion", "[^"]*"\)', "StringStruct(""FileVersion"", ""$Version"")" `
  -replace 'StringStruct\("ProductVersion", "[^"]*"\)', "StringStruct(""ProductVersion"", ""$Version"")" | Set-Content $vi -Encoding utf8

# 1. frontend
Push-Location (Join-Path $Root "frontend")
try {
  if (-not (Test-Path "node_modules")) { npm ci --no-fund --no-audit; if ($LASTEXITCODE) { throw "npm ci failed" } }
  npm run build; if ($LASTEXITCODE) { throw "frontend build failed" }
} finally { Pop-Location }

# 2. freeze
& $Py -m PyInstaller --noconfirm --clean --distpath (Join-Path $Pkg "dist") --workpath (Join-Path $Pkg "build") (Join-Path $Pkg "PhysiqueLapse.spec")
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$app = Join-Path $Pkg "dist\PhysiqueLapse"
$size = [math]::Round(((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "==> App folder: $app ($size MB)" -ForegroundColor Cyan

$out = Join-Path $Pkg "out"
New-Item -ItemType Directory -Force $out | Out-Null

# 3. portable zip
if (-not $NoZip) {
  $zip = Join-Path $out "PhysiqueLapse-$Version-portable.zip"
  if (Test-Path $zip) { Remove-Item $zip }
  Write-Host "==> Zipping $zip" -ForegroundColor Cyan
  Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal
}

# 4. installer
if (-not $NoInstaller) {
  $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($iscc) {
    & $iscc "/DAppVersion=$Version" "/O$out" (Join-Path $Pkg "installer.iss")
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
  } else {
    Write-Warning "Inno Setup 6 not found: skipped the installer (the portable zip works on its own). Install it from https://jrsoftware.org/isinfo.php to build PhysiqueLapse-Setup.exe."
  }
}
Write-Host "==> Done. Output in $out" -ForegroundColor Green
