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

$legacyRuntimeEntries = @(
    ".env", ".git", ".github", ".playwright-cli", ".pytest_cache", ".ruff_cache",
    "browser-profile", "browser-profiles", "build", "daily-intel-data",
    "daily-intelligence", "daily_intelligence_skill.egg-info", "data", "dist",
    "edge-profile", "raw_html", "screenshots"
)
$sameDirectory = [String]::Equals(
    [IO.Path]::GetFullPath($skillDir).TrimEnd([IO.Path]::DirectorySeparatorChar),
    $targetDir.TrimEnd([IO.Path]::DirectorySeparatorChar),
    [StringComparison]::OrdinalIgnoreCase
)
if (-not $sameDirectory) {
    $targetPrefix = $targetDir.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    foreach ($entry in $legacyRuntimeEntries) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $targetDir $entry))
        if (-not $candidate.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove an install artifact outside the skill target: $candidate"
        }
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Recurse -Force
        }
    }
}

$excludedDirs = @(
    ".agents", ".git", ".playwright-cli", ".pytest_cache", ".ruff_cache", "__pycache__",
    "build", "dist", "data", "daily-intelligence", "daily-intel-data", "browser-profile",
    "browser-profiles", "edge-profile", "raw_html", "screenshots",
    "daily_intelligence_skill.egg-info"
)
$excludedFiles = @(".env", "*.cookies.json", "*.har", "*.storage-state.json")
if (-not $sameDirectory) {
    & robocopy $skillDir $targetDir /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP `
        /XD $excludedDirs /XF $excludedFiles
    if ($LASTEXITCODE -ge 8) {
        throw "Skill synchronization failed with robocopy exit code $LASTEXITCODE"
    }
}

$packageRoot = if ($Editable) { $skillDir } else { $targetDir }
$package = if ($Dev) { "${packageRoot}[dev]" } else { $packageRoot }
$pipArgs = @("-m", "pip", "install")
if ($Editable) {
    $pipArgs += "-e"
}
$pipArgs += $package
& python @pipArgs
if (-not $sameDirectory) {
    foreach ($entry in $legacyRuntimeEntries) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $targetDir $entry))
        if (-not $candidate.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a post-install artifact outside the skill target: $candidate"
        }
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Recurse -Force
        }
    }
}
Write-Host "Synchronized skill: $targetDir"
Write-Host "Installed package. Windows collection uses system Microsoft Edge; no bundled browser download is required."
Write-Host "Run: daily-intel --help"
