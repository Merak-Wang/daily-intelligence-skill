param(
    [switch]$Editable,
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$skillDir = Split-Path $PSScriptRoot -Parent
$hermesHome = if ($env:HERMES_HOME) {
    [IO.Path]::GetFullPath($env:HERMES_HOME)
} else {
    [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "hermes"))
}
$skillsRoot = [IO.Path]::GetFullPath((Join-Path $hermesHome "skills"))
$targetDir = [IO.Path]::GetFullPath((Join-Path $skillsRoot "research\daily-intelligence"))
if (-not $targetDir.StartsWith($skillsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to synchronize outside the Hermes skills directory: $targetDir"
}
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

$excludedDirs = @(
    ".agents", ".git", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "dist", "data",
    "browser-profiles", "daily_intelligence_skill.egg-info"
)
& robocopy $skillDir $targetDir /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD $excludedDirs
if ($LASTEXITCODE -ge 8) {
    throw "Skill synchronization failed with robocopy exit code $LASTEXITCODE"
}

$packageRoot = if ($Editable) { $skillDir } else { $targetDir }
$package = if ($Dev) { "${packageRoot}[dev]" } else { $packageRoot }
$pipArgs = @("-m", "pip", "install")
if ($Editable) {
    $pipArgs += "-e"
}
$pipArgs += $package
& python @pipArgs
Write-Host "Synchronized skill: $targetDir"
Write-Host "Installed package. Windows collection uses system Microsoft Edge; no bundled browser download is required."
Write-Host "Run: daily-intel --help"
