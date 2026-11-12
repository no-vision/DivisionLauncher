#!/usr/bin/env bash
set -euo pipefail
launcher_root="$(cd -- "$(dirname -- "$0")" && pwd)"
export DOTNET_CLI_HOME="$launcher_root/.tools/cli"
export DIVISION_DESKTOP_RUNTIME_DIR="${DIVISION_DESKTOP_RUNTIME_DIR-${XDG_RUNTIME_DIR-}}"
export XDG_RUNTIME_DIR="$launcher_root/.tools/runtime"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
export NUGET_PACKAGES="${NUGET_PACKAGES-$launcher_root/.tools/packages}"
export DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
if [[ -n "${DIVISION_DOTNET-}" ]]; then sdk="$DIVISION_DOTNET"
elif [[ -x "$launcher_root/.tools/dotnet/dotnet" ]]; then sdk="$launcher_root/.tools/dotnet/dotnet"
else sdk=dotnet
fi
cd "$launcher_root"
exec "$sdk" "$@"
