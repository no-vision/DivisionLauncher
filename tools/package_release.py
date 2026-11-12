"""Package a published launcher directory; no account, game or runtime data.

Example: python3 package_release.py <publish-folder> 0.2.1 launcher-0.2.1.zip
Publish LauncherDesktop including its dependencies first. A portable publish
includes Avalonia runtime assets for supported platforms; use --runtime for a
platform-specific publish. The archive has integrity hashes, not a signature.
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
    parser.add_argument('--runtime', default='portable')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(\.\d+)?', args.version):
        parser.error('Use a numeric release version, such as 0.2.1')
    source = args.publish.resolve()
    if args.output.resolve().is_relative_to(source):
        parser.error('Write the archive outside the publish directory')
    files = sorted(p for p in source.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in source.rglob('*')):
        parser.error('Publish directory must not contain symlinks')
    required = ['LauncherDesktop.dll', 'LauncherDesktop.deps.json', 'LauncherDesktop.runtimeconfig.json']
    if any(not (source / name).is_file() for name in required):
        parser.error('Incomplete launcher publish directory')
    manifest = {'product': 'DivisionPrivateLauncher', 'version': args.version,
                'runtime': args.runtime, 'files': []}
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(source).as_posix()
            payload = path.read_bytes()
            manifest['files'].append({'path': name, 'length': len(payload),
                                      'sha256': hashlib.sha256(payload).hexdigest()})
            archive.write(path, 'app/' + name)
        archive.writestr('release.json', json.dumps(manifest, indent=2))
    print(f'Packaged {len(files)} launcher files as {args.output}')


if __name__ == '__main__':
    main()
