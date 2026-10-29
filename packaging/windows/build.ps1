# Build Windows executables (and the installer if Inno Setup is installed).
#   pwsh packaging/windows/build.ps1
# Output: dist/windows/sysglance.exe, sysglance-dev.exe, sysglance-classic.exe,
#         dist/windows/sysglance-<version>-windows-x64.zip and -setup.exe
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot/../..")

python -m pip install --upgrade pip
python -m pip install . pyinstaller
$version = python -c "import sysglance; print(sysglance.__version__)"

$editions = @{ "sysglance" = "friendly"; "sysglance-dev" = "dev"; "sysglance-classic" = "classic" }
foreach ($name in $editions.Keys) {
    $edition = $editions[$name]
    python -m PyInstaller --noconfirm --clean --onefile --console `
        --name $name `
        --collect-submodules rich `
        --collect-submodules sysglance `
        --hidden-import msvcrt --hidden-import winreg --hidden-import ctypes.wintypes `
        --distpath dist/windows --workpath build/pyinstaller --specpath build/pyinstaller `
        "packaging/windows/launch_$edition.py"
    if ($LASTEXITCODE) { throw "PyInstaller failed for $name" }
}

# Smoke test: each exe must render one snapshot and exit cleanly.
foreach ($name in $editions.Keys) {
    & "dist/windows/$name.exe" --once | Out-Null
    if ($LASTEXITCODE) { throw "$name.exe --once failed" }
}

Compress-Archive -Force -Path dist/windows/*.exe, LICENSE, README.md `
    -DestinationPath "dist/windows/sysglance-$version-windows-x64.zip"

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc -and (Test-Path "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")) {
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if ($iscc) {
    & $iscc "/DAppVersion=$version" packaging/windows/sysglance.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup not found; skipping installer (zip was built)."
}
