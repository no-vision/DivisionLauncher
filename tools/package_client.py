#!/usr/bin/env python3
"""Build a standalone client bundle from a clean, self-contained Linux publish.
No game, server, account, certificate, capture or development runtime is included.
External adapter dependencies are explicitly supplied and their licenses retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

HERE = Path(__file__).resolve().parents[1]
CLIENT_FILES = ['proton_client.py', 'tutorial_history.py', 'overlay_frame.py', 'overlay_ui.py',
                'startup_agent.js', 'private_friends_agent.js', 'private_friends_feed.py', 'aim_diagnostic.js', 'overlay/agent.js',
                'overlay/bridge.c', 'overlay/packet_roles.json']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('publish', type=Path)
    parser.add_argument('version')
    parser.add_argument('output', type=Path)
    parser.add_argument('--adapter-source', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Use a numeric version')
    source = args.publish.resolve()
    for name in ['LauncherDesktop', 'LauncherDesktop.dll', 'libcoreclr.so',
                 'LauncherDesktop.runtimeconfig.json', 'ThirdPartyNotices/Packages.tsv'] + ['client/'+f for f in CLIENT_FILES]:
        if not (source/name).is_file():
            parser.error('Incomplete self-contained Linux publish: ' + name)
    adapter = args.adapter_source.resolve()
    required = ['frida-server-17.4.0.exe', 'deps-17.4.0/frida-17.4.0.dist-info/licenses/COPYING',
                'deps-17.4.0/typing_extensions-4.12.2.dist-info/LICENSE']
    for name in required:
        if not (adapter/name).is_file():
            parser.error('Missing adapter dependency/license: ' + name)
    # No directory-wide copy of research folders or persistent launcher state.
    for file in source.rglob('*'):
        if file.is_symlink() or (file.is_file() and (file.suffix.lower() in ('.sqlite3', '.db', '.pem', '.key', '.pfx') or
                file.name.lower() in ('thedivision.exe', 'uplay_r1_loader64.dll'))):
            parser.error('Publish folder contains state, game data or symlinks: ' + str(file))
    if args.output.exists():
        parser.error('Output already exists; use a new release artifact')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        root = Path(temporary)/('DivisionPrivateLauncher-'+args.version+'-linux-x64')
        launcher = root
        shutil.copytree(source, launcher/'app')
        shutil.copytree(adapter/'deps-17.4.0', launcher/'adapter/deps-17.4.0',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'REQUESTED', 'INSTALLER', 'RECORD'))
        shutil.copy2(adapter/'frida-server-17.4.0.exe', launcher/'adapter/frida-server-17.4.0.exe')
        (launcher/'addons').mkdir()
        shutil.copy2(HERE/'docs/remote-servers.txt', root/'README.txt')
        (root/'launch.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
BundleRoot="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
StateRoot="$BundleRoot"
LegacyState="$BundleRoot/Production/launcher"
if [[ ! -d "$StateRoot/profiles" && ! -f "$StateRoot/connections.sqlite3" && ! -f "$StateRoot/active-release.txt" ]] &&
   [[ -d "$LegacyState/profiles" || -f "$LegacyState/connections.sqlite3" || -f "$LegacyState/active-release.txt" ]]; then
  StateRoot="$LegacyState"
fi
App="$BundleRoot/app"
Selection="$StateRoot/active-release.txt"
if [[ -f "$Selection" ]]; then
  Key="$(cat "$Selection")"
  if [[ "$Key" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+(\\.[0-9]+)?-linux-x64$ && -x "$StateRoot/releases/$Key/LauncherDesktop" ]]; then
    App="$StateRoot/releases/$Key"
  else
    echo 'Installed update is not a self-contained Linux release; using bundled launcher.' >&2
  fi
fi
exec "$App/LauncherDesktop" "$BundleRoot" "$@"
''')
        (root/'launch.sh').chmod(0o755)
        (launcher/'app/LauncherDesktop').chmod(0o755)
        files = [{'path': str(p.relative_to(root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(root.rglob('*')) if p.is_file()]
        (root/'bundle-manifest.json').write_text(json.dumps({'version': args.version, 'runtime': 'linux-x64',
            'files': files, 'signature': None}, indent=2)+'\n')
        def public_metadata(info):
            # tar otherwise stores the packaging user's login and local group.
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            info.pax_headers = {key: value for key, value in info.pax_headers.items()
                                if key not in ('uid', 'gid', 'uname', 'gname', 'atime', 'ctime', 'mtime')}
            return info
        with tarfile.open(args.output, 'w:gz') as archive:
            archive.add(root, arcname=root.name, filter=public_metadata)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name+'.sha256').write_text(digest+'  '+args.output.name+'\n')
    print(f'Packaged {len(files)} files: {args.output}')
    print('Integrity checksum only, not a publisher signature. Distribute through your trusted release channel.')


if __name__ == '__main__':
    main()
