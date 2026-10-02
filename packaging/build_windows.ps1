# Build LULC Fetch for Windows: dist\LULC Fetch\LULC Fetch.exe and dist\LULC-Fetch-Windows.zip
# Run from the project folder in PowerShell (with Python and:  pip install -e ".[web]" pyinstaller).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Remove-Item -Recurse -Force build, "dist\LULC Fetch", "dist\LULC-Fetch-Windows.zip" -ErrorAction SilentlyContinue
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\lulc_fetch.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Compress-Archive -Path "dist\LULC Fetch" -DestinationPath "dist\LULC-Fetch-Windows.zip"
Write-Host "Built: dist\LULC Fetch\LULC Fetch.exe and dist\LULC-Fetch-Windows.zip"
