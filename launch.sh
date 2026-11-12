#!/usr/bin/env bash
set -euo pipefail
ProjectRoot="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LauncherProject="LauncherDesktop"
if [[ "${1-}" == "--terminal" ]]; then
  LauncherProject="Launcher"
  shift
fi
StateRoot="$ProjectRoot"
LegacyState="$ProjectRoot/Production/launcher"
if [[ ! -d "$StateRoot/profiles" && ! -f "$StateRoot/connections.sqlite3" && ! -f "$StateRoot/active-release.txt" ]] &&
   [[ -d "$LegacyState/profiles" || -f "$LegacyState/connections.sqlite3" || -f "$LegacyState/active-release.txt" ]]; then
  StateRoot="$LegacyState"
fi
LauncherDll="$ProjectRoot/src/$LauncherProject/bin/Debug/net10.0/$LauncherProject.dll"
if [[ "$LauncherProject" == "LauncherDesktop" && -f "$StateRoot/active-release.txt" ]]; then
  ReleaseKey="$(cat "$StateRoot/active-release.txt")"
  if [[ "$ReleaseKey" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?-[a-z0-9.-]+$ && -f "$StateRoot/releases/$ReleaseKey/LauncherDesktop.dll" ]]; then
    LauncherDll="$StateRoot/releases/$ReleaseKey/LauncherDesktop.dll"
  else
    echo 'Installed launcher selection is invalid; using the bundled version.' >&2
  fi
fi
if [[ "$(uname -s)" == "Linux" ]]; then
  exec "$ProjectRoot/dotnet.sh" "$LauncherDll" "$ProjectRoot" "$@"
fi
exec dotnet "$LauncherDll" "$ProjectRoot" "$@"
