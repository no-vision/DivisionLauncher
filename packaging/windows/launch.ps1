$ErrorActionPreference = 'Stop'
$BundleRoot = $PSScriptRoot
$StateRoot = $BundleRoot
$LegacyState = Join-Path $BundleRoot 'Production/launcher'
$HasCurrentState = (Test-Path (Join-Path $StateRoot 'profiles')) -or (Test-Path (Join-Path $StateRoot 'connections.sqlite3')) -or (Test-Path (Join-Path $StateRoot 'active-release.txt'))
$HasLegacyState = (Test-Path (Join-Path $LegacyState 'profiles')) -or (Test-Path (Join-Path $LegacyState 'connections.sqlite3')) -or (Test-Path (Join-Path $LegacyState 'active-release.txt'))
if (-not $HasCurrentState -and $HasLegacyState) { $StateRoot = $LegacyState }
$App = Join-Path $BundleRoot 'app'
$Selection = Join-Path $StateRoot 'active-release.txt'
if (Test-Path -LiteralPath $Selection) {
    $ReleaseKey = (Get-Content -LiteralPath $Selection -Raw).Trim()
    if ($ReleaseKey -match '^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?-win-x64$') {
        $Candidate = Join-Path $StateRoot ('releases\' + $ReleaseKey)
        if ((Test-Path -LiteralPath (Join-Path $Candidate 'LauncherDesktop.exe')) -and
            (Test-Path -LiteralPath (Join-Path $Candidate 'coreclr.dll'))) {
            $App = $Candidate
        } else {
            Write-Warning 'The selected update is incomplete; using the bundled launcher.'
        }
    } else {
        Write-Warning 'The selected update is not a Windows x64 release; using the bundled launcher.'
    }
}
& (Join-Path $App 'LauncherDesktop.exe') $BundleRoot @args
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
