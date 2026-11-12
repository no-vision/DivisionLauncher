#!/usr/bin/env python3
"""Retain restored NuGet license notices alongside a self-contained publish."""
import argparse
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('publish', type=Path)
parser.add_argument('packages', type=Path)
parser.add_argument('--assembly', default='LauncherDesktop')
args = parser.parse_args()
manifest = json.loads((args.publish/(args.assembly+'.deps.json')).read_text())
output = args.publish/'ThirdPartyNotices'
output.mkdir(exist_ok=True)
rows = ['Package\tVersion\tLicense\tProject']
for key, value in sorted(manifest['libraries'].items()):
    if value['type'].lower() not in ('package', 'runtimepack'):
        continue
    name, version = key.removeprefix('runtimepack.').rsplit('/', 1)
    package = args.packages/name.lower()/version
    specs = list(package.glob('*.nuspec'))
    if len(specs) != 1:
        raise SystemExit('Missing restored package metadata: '+key)
    spec = ET.parse(specs[0])
    def field(tag):
        node = spec.find('.//{*}'+tag)
        return node.text if node is not None and node.text else ''
    rows.append('\t'.join([name,version,field('license'),field('projectUrl')]))
    target = output/(name+'-'+version)
    target.mkdir(exist_ok=True)
    shutil.copy2(specs[0],target/specs[0].name)
    for file in package.rglob('*'):
        if file.is_file() and file.name.lower().startswith(('license','copying','notice','third-party-notices')):
            destination=target/file.relative_to(package)
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(file,destination)
(output/'Packages.tsv').write_text('\n'.join(rows)+'\n')
print(f'Retained notices and metadata for {len(rows)-1} runtime packages.')
