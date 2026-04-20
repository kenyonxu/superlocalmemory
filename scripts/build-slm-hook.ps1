# LLD-06 §5.1 — Windows PowerShell build script for slm-hook onedir binary.
# Runs AST generator -> PyInstaller -> smoke test.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..")

Write-Host "[build-slm-hook] repo: $RepoRoot"

$Py = if ($env:SLM_BUILD_PYTHON) { $env:SLM_BUILD_PYTHON } else { "python" }

& $Py -m pip install --quiet --upgrade `
    "pyinstaller==6.15.0" "pyinstaller-hooks-contrib==2026.1"

$Dest = Join-Path $RepoRoot "src\superlocalmemory\hook_binary_entry.py"
& $Py (Join-Path $ScriptDir "build_entry.py") `
    --repo-root $RepoRoot `
    --dest $Dest
Write-Host "[build-slm-hook] entry emitted: $Dest"

Set-Location $RepoRoot
& $Py -m PyInstaller `
    (Join-Path $ScriptDir "slm-hook.spec") `
    --clean --noconfirm `
    --distpath (Join-Path $RepoRoot "dist") `
    --workpath (Join-Path $RepoRoot "build\pyi")

$Binary = Join-Path $RepoRoot "dist\slm-hook\slm-hook.exe"
if (-not (Test-Path $Binary)) {
    Write-Error "[build-slm-hook] binary not produced at $Binary"
    exit 2
}

Write-Host "[build-slm-hook] smoke test ..."
$out = "" | & $Binary
if ($out -ne "{}") {
    Write-Error "[build-slm-hook] expected '{}', got: $out"
    exit 3
}
Write-Host "[build-slm-hook] OK"
