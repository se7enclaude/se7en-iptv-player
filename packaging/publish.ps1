<#
  se7en — publish a version on GitHub Releases (every installed se7en then updates itself).

    powershell -ExecutionPolicy Bypass -File packaging\publish.ps1
    powershell -ExecutionPolicy Bypass -File packaging\publish.ps1 -Notes packaging\notes.md

  Prerequisites (once):
    1. a GitHub account and a repository for the releases, e.g. "moncompte/se7en"
       (its name is stored in packaging\update.json by build.ps1 -Repo moncompte/se7en)
    2. GitHub CLI logged in:  build\tools\gh\bin\gh.exe auth login

  It uploads the files built by build.ps1:
    se7en-IPTV-Player-Setup-<v>.exe      to send to new users
    se7en-IPTV-Player-Update-<v>.exe     downloaded by automatic updates
    se7en-IPTV-Player-Portable-<v>.zip   portable edition
    latest.json              update feed read by every se7en
    SHA256SUMS.txt           checksums
#>
param([string]$Repo = "", [string]$Notes = "packaging\notes.md", [switch]$CreateRepo)
# "Continue": in Windows PowerShell 5.1, "Stop" turns any stderr line of gh (even an expected "not found")
# into a fatal error. Every gh call below checks $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = "backend\.venv\Scripts\python.exe"

$gh = @("build\tools\gh\bin\gh.exe", (Get-Command gh -ErrorAction SilentlyContinue).Source) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $gh) { throw "GitHub CLI not found (build\tools\gh\bin\gh.exe)" }
& $gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in to GitHub. Run:  $gh auth login" }

if (-not $Repo -and (Test-Path "packaging\update.json")) { $Repo = (Get-Content "packaging\update.json" | ConvertFrom-Json).repo }
if (-not $Repo) { throw "No repository: pass -Repo owner/name (or build with -Repo)" }
$Version = (& $Py packaging\release.py version).Trim()
$Tag = "v$Version"

$files = @("dist\se7en-IPTV-Player-Setup-$Version.exe", "dist\se7en-IPTV-Player-Update-$Version.exe", "dist\se7en-IPTV-Player-Portable-$Version.zip", "dist\se7en-IPTV-Player-Portable-Update-$Version.zip")
foreach ($f in $files) { if (-not (Test-Path $f)) { throw "Missing $f - run packaging\build.ps1 first" } }
# The installer is published under a permanent name, so the link sent to friends never changes:
# https://github.com/<repo>/releases/latest/download/se7en-IPTV-Player-Setup.exe  (hard link: no extra disk space)
Remove-Item "dist\se7en-IPTV-Player-Setup.exe" -ErrorAction SilentlyContinue
New-Item -ItemType HardLink -Path "dist\se7en-IPTV-Player-Setup.exe" -Target (Resolve-Path "dist\se7en-IPTV-Player-Setup-$Version.exe") | Out-Null
$files[0] = "dist\se7en-IPTV-Player-Setup.exe"
& $Py packaging\release.py manifest $Repo $Notes
$files += @("dist\latest.json", "dist\SHA256SUMS.txt")

& $gh repo view $Repo 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  if (-not $CreateRepo) { throw "Repository $Repo does not exist (add -CreateRepo to create it)" }
  & $gh repo create $Repo --public --description "se7en IPTV Player - lecteur IPTV pour Windows (Xtream Codes, M3U, XMLTV)"
  if ($LASTEXITCODE -ne 0) { throw "Could not create $Repo" }
}

# A release needs a commit to tag: give an empty repository a short README first.
& $gh api "repos/$Repo/commits?per_page=1" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  $readme = @"
# se7en IPTV Player

Lecteur IPTV pour Windows : ton abonnement Xtream Codes ou ta liste M3U, avec le guide des programmes.

**Télécharger :** [se7en-IPTV-Player-Setup (Windows 10/11)](https://github.com/$Repo/releases/latest/download/se7en-IPTV-Player-Setup.exe)

Une fois installé, se7en se met à jour tout seul. Au premier lancement, Windows SmartScreen peut afficher
un avertissement (programme non signé) : « Informations complémentaires » → « Exécuter quand même ».

se7en IPTV Player ne fournit aucune chaîne ni aucun abonnement.
"@
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($readme))
  & $gh api -X PUT "repos/$Repo/contents/README.md" -f message="se7en IPTV Player" -f content=$b64 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not initialise $Repo (README)" }
}

& $gh release view $Tag --repo $Repo 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { throw "Release $Tag already exists on $Repo - bump the version in backend\medialab\__init__.py" }

$notesArgs = if (Test-Path $Notes) { @("--notes-file", $Notes) } else { @("--notes", "se7en IPTV Player $Version") }
Write-Host "Publishing se7en IPTV Player $Version on github.com/$Repo ..." -ForegroundColor Cyan
& $gh release create $Tag @files --repo $Repo --title "se7en IPTV Player $Version" --latest @notesArgs
if ($LASTEXITCODE -ne 0) { throw "Publishing failed" }
Write-Host "Published. Installed copies will update themselves within a few hours (or at their next launch)." -ForegroundColor Green
Write-Host "Link for new users: https://github.com/$Repo/releases/latest/download/se7en-IPTV-Player-Setup.exe"
