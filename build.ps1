$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'tools/build.py') @args
exit $LASTEXITCODE
