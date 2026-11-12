#!/usr/bin/env python3
"""Package the self-contained Windows launcher with the native private-game adapter.
The published executable is cross-built. This does not verify Windows execution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('publish', type=Path)
    parser.add_argument('version')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Use a numeric version')
    source = args.publish.resolve()
    required = ('LauncherDesktop.exe', 'LauncherDesktop.dll', 'LauncherDesktop.deps.json',
                'LauncherDesktop.runtimeconfig.json', 'coreclr.dll', 'hostfxr.dll',
                'ThirdPartyNotices/Packages.tsv', 'python/python.exe', 'python/python313._pth',
                'client/windows_client.py', 'client/windows_environment_agent.js')
    if any(not (source / name).is_file() for name in required):
        parser.error('Incomplete self-contained Windows x64 publish')
    config = json.loads((source / 'LauncherDesktop.runtimeconfig.json').read_text())
    if 'includedFrameworks' not in config.get('runtimeOptions', {}):
        parser.error('Publish must include its runtime')
    deps = json.loads((source / 'LauncherDesktop.deps.json').read_text())
    if not deps['runtimeTarget']['name'].endswith('/win-x64'):
        parser.error('Publish is not win-x64')
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            parser.error('Symlinks are not allowed')
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if path.suffix.lower() in ('.sqlite3', '.db', '.pem', '.key', '.pfx') or path.name.lower() in (
                'thedivision.exe', 'uplay_r1_loader64.dll', 'viewer.url') or any(
                part.lower() in ('profiles', 'captures', 'runtime') for part in relative.parts):
            parser.error('Publish contains private state or game data: ' + str(relative))
        files['app/' + relative.as_posix()] = path.read_bytes()
    templates = Path(__file__).resolve().parents[1] / 'packaging/windows'
    for name in ('launch.cmd', 'launch.ps1', 'README.txt'):
        text = (templates / name).read_text().replace('0.4.0', args.version)
        files[name] = text.replace('\n', '\r\n').encode('utf-8')
    manifest = {'version': args.version, 'runtime': 'win-x64', 'gameLaunchSupported': True, 'nativeWindowsPlaytest': 'pending',
                'signature': None, 'files': [
                    {'path': name, 'length': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                    for name, data in sorted(files.items())]}
    files['bundle-manifest.json'] = (json.dumps(manifest, indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f'DivisionPrivateLauncher-{args.version}-win-x64/'
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(prefix+name, data)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name+'.sha256').write_text(digest+'  '+args.output.name+'\n')
    print(f'Packaged {len(files)} files: {args.output}')


if __name__ == '__main__':
    main()
