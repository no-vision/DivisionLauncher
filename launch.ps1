param([string]$ProfileDatabase)
$ProjectRoot = $PSScriptRoot
$StateRoot = $ProjectRoot
$LegacyState = Join-Path $ProjectRoot 'Production/launcher'
$HasCurrentState = (Test-Path (Join-Path $StateRoot 'profiles')) -or (Test-Path (Join-Path $StateRoot 'connections.sqlite3')) -or (Test-Path (Join-Path $StateRoot 'active-release.txt'))
$HasLegacyState = (Test-Path (Join-Path $LegacyState 'profiles')) -or (Test-Path (Join-Path $LegacyState 'connections.sqlite3')) -or (Test-Path (Join-Path $LegacyState 'active-release.txt'))
if (-not $HasCurrentState -and $HasLegacyState) { $StateRoot = $LegacyState }
$LauncherDll = Join-Path $ProjectRoot "src/LauncherDesktop/bin/Debug/net10.0/LauncherDesktop.dll"
$ActiveRelease = Join-Path $StateRoot "active-release.txt"
if (Test-Path $ActiveRelease) {
    $ReleaseKey = (Get-Content $ActiveRelease -Raw).Trim()
    if ($ReleaseKey -match '^\d+\.\d+\.\d+(\.\d+)?-[a-z0-9.-]+$') {
        $InstalledDll = Join-Path $StateRoot "releases/$ReleaseKey/LauncherDesktop.dll"
        if (Test-Path $InstalledDll) { $LauncherDll = $InstalledDll }
    }
}
if ($ProfileDatabase) {
    & dotnet $LauncherDll $ProjectRoot $ProfileDatabase
} else {
    & dotnet $LauncherDll $ProjectRoot
}
exit $LASTEXITCODE
