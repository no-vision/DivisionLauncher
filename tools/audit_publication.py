#!/usr/bin/env python3
"""Check Git publication candidates without printing matched secret values.

This is a leak regression guard, not proof that opaque protocol data is public-safe.
Deep mode additionally scans raw database pages and decompressed geometry pages.
"""
from pathlib import Path, PurePosixPath
import argparse
import gzip
import json
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTENT = 'Production/server/content/'
PATTERNS = {
    # Also catch bare home directories, Windows/JSON separator variants and
    # legacy Windows profile locations. Never store an operator denylist here.
    'personal-home-path': re.compile(rb'(?:/home/|/Users/|(?i:[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+))[A-Za-z0-9_.@+-]+'),
    'machine-storage-path': re.compile(rb'/(?:mnt|media|Volumes|root)/[A-Za-z0-9_.@+-]+'),
    'cloud-access-key': re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    'provider-token': re.compile(rb'(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{20,}|sk-proj-[A-Za-z0-9_-]{40,})'),
    'jwt': re.compile(rb'eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{12,}'),
    # A header followed by the word "fixture" is not a usable private key.
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=]{32,}'),
    'connection-password': re.compile(rb'(?im)(?:^|[;"\x27])(?:Password|Pwd)\s*=\s*(?!YOUR_SECRET(?:;|$))[^\s;"\x27]{3,}'),
    'credential-url': re.compile(rb'://[^\s/:@\x00]{1,80}:[^\s/@\x00]{1,100}@'),
}
PRIVATE_DIRS = {'runtime', 'profiles', 'private-game', 'captures', 'backups', '.cache', '.pgc', '.aws', '.ssh', 'secrets', 'bin', 'obj', 'artifacts', '.tools'}
PRIVATE_SUFFIXES = {'.key', '.pem', '.crt', '.pfx', '.p12', '.connection', '.log', '.bak', '.backup', '.dump', '.dmp', '.pcap', '.pcapng', '.har', '.user', '.suo'}
PRIVATE_NAMES = {'.netrc', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'credentials.json'}
BUILD_SUFFIXES = {'.dll', '.exe', '.pdb', '.so', '.dylib', '.zip', '.7z', '.tar', '.xz', '.zst'}
DATABASE_SUFFIXES = {'.sqlite3', '.sqlite', '.db', '.db3'}
UTF16_RUNS = [(re.compile(rb'(?:[\x09\x0a\x0d\x20-\x7e]\x00){8,}'), 'utf-16le'),
              (re.compile(rb'(?:\x00[\x09\x0a\x0d\x20-\x7e]){8,}'), 'utf-16be')]


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def candidates(root):
    return sorted(set(p.decode('utf-8') for p in git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0') if p))


def path_issues(name, content_paths):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        return ['invalid-path']
    issues = []
    if set(p.parts) & PRIVATE_DIRS or p.suffix.lower() in PRIVATE_SUFFIXES or p.name in PRIVATE_NAMES or p.name.startswith('service-account') and p.suffix == '.json':
        issues.append('private-or-generated-file')
    if p.name == '.env' or p.name.startswith('.env.') and p.name != '.env.example':
        issues.append('environment-file')
    if p.suffix.lower() in DATABASE_SUFFIXES and name not in content_paths:
        issues.append('unlisted-database')
    if name.startswith((CONTENT, 'content-packs/')) and name not in content_paths:
        issues.append('unlisted-content')
    if p.name.endswith(('-wal', '-shm', '-journal')):
        issues.append('database-sidecar')
    if p.suffix.lower() in BUILD_SUFFIXES or p.suffix.lower() == '.gz' and name not in content_paths:
        issues.append('build-or-archive-file')
    return issues


def scan_stream(stream):
    found = set()
    tail = b''
    while chunk := stream.read(4 * 1024 * 1024):
        data = tail + chunk
        # Only decode actual alternating-byte UTF-16 runs. Removing arbitrary
        # zero bytes invents credential-like strings across unrelated DB values.
        forms = [data]
        if b'\0' in data:
            for pattern, encoding in UTF16_RUNS:
                forms.extend(match[0].decode(encoding).encode('ascii') for match in pattern.finditer(data))
        for form in forms:
            # JSON and percent-encoded separators must not hide a local path.
            form = form.replace(b'\\/', b'/')
            form = re.sub(rb'%2[fF]', b'/', form)
            form = re.sub(rb'%5[cC]', lambda _: b'\\', form)
            for name, pattern in PATTERNS.items():
                if name not in found and pattern.search(form):
                    found.add(name)
        tail = data[-2048:]
    return sorted(found)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def check_private_tables(path, tables):
    issues = []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in present:
                issues.append('missing-private-table:' + table)
            elif db.execute('SELECT 1 FROM ' + quote(table) + ' LIMIT 1').fetchone():
                issues.append('private-state:' + table)
    return issues


def check_protocol(path):
    expected = {
        'Account': b'facefeed-0000-0000-0000-000000000004',
        'Username': b'\x15private-template-name',
        'Player': bytes.fromhex('facefeed000000000000000000000001'),
        'PrimaryWeapon': bytes.fromhex('facefeed000000000000000000000002'),
        'PistolWeapon': bytes.fromhex('facefeed000000000000000000000003'),
        'World': b'%PRIVATE-TEMPLATE-WORLD-IDENTIFIER0000',
    }
    issues = []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for kind, sentinel in expected.items():
            rows = db.execute('SELECT p.payload,x.offset,x.width FROM patch x JOIN packet p ON p.id=x.packet WHERE x.kind=?', (kind,)).fetchall()
            if not rows or any(bytes(data)[offset:offset + width] != sentinel for data, offset, width in rows):
                issues.append('non-synthetic-protocol-field:' + kind)
        service_id = re.compile(rb'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{16}')
        for (payload,) in db.execute("SELECT payload FROM packet WHERE bundle='lobby.initial'"):
            if any(not value.startswith(b'FACEFEED-0000-0000-') for value in service_id.findall(payload)):
                issues.append('captured-service-process-identifier')
                break
    return issues


def check_protocol_identity_fields(path, fields):
    """Check the typed fields discovered by the private identity sweep.

    The public policy contains synthetic replacements only, never a denylist
    of the original people's identifiers.
    """
    issues = []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for field in fields:
            table = field['table']
            if table == 'packet':
                row = db.execute('SELECT payload,message,bundle,ordinal FROM packet WHERE id=?', (field['packet'],)).fetchone()
                valid = row is not None and row[1:] == (field['message'], field['bundle'], field['ordinal'])
                data = row[0] if row else b''
                label = 'packet:' + str(field['packet']) + ':' + str(field['offset'])
            elif table == 'blob':
                row = db.execute('SELECT data FROM blob WHERE name=?', (field['name'],)).fetchone()
                valid = row is not None
                data = row[0] if row else b''
                label = 'blob:' + field['name'] + ':' + str(field['offset'])
            elif table == 'reference':
                row = db.execute('SELECT identity FROM reference WHERE mode=? AND token=?', (field['mode'], field['token'])).fetchone()
                valid = row is not None and row[0] == field['expectedHex']
                label = 'reference:' + field['mode'] + ':' + str(field['token'])
            else:
                raise ValueError('unsupported identity policy table')
            if table != 'reference':
                valid = valid and bytes(data)[field['offset']:field['offset'] + field['width']].hex() == field['expectedHex']
            if not valid:
                issues.append('non-synthetic-template-identity:' + label)
    return issues


def audit(root, deep=False):
    rows = json.loads((root / 'manifests/content.json').read_text())['files']
    logical_paths = {row['path'] for row in rows}
    policy = json.loads((root / 'manifests/publication-policy.json').read_text())
    storage_file = root / 'manifests/content-storage.json'
    storage = json.loads(storage_file.read_text()) if storage_file.exists() else {'files': []}
    packed_paths = {row['path'] for row in storage['files']}
    part_paths = {part['path'] for row in storage['files'] for part in row['parts']}
    content_paths = (logical_paths - packed_paths) | part_paths
    files = candidates(root)
    findings = []
    def add(path, issue):
        findings.append({'path': path, 'issue': issue})
    # A clean working file must not hide an older staged secret. Require the
    # index and working tree to agree before approving an intended commit.
    for name in git(root, 'diff', '--name-only', '-z').split(b'\0'):
        if name:
            add(name.decode(), 'index-differs-from-audited-worktree')
    history = set(git(root, 'rev-list', '--all').decode().splitlines())
    if history - set(policy.get('reviewedHistoryCommits', [])):
        add('.git', 'history-requires-separate-secret-review')
    scanned = 0
    for name in files:
        if any(name.startswith(prefix) for prefix in policy.get('forbiddenPaths', [])):
            add(name, 'private-source-outside-repository-scope')
        client_core = policy.get('clientCorePath', 'Production/server/src/Core/')
        if 'allowedClientCoreFiles' in policy and name.startswith(client_core) and name[len(client_core):] not in policy['allowedClientCoreFiles']:
            add(name, 'non-client-core-source')
        for issue in path_issues(name, content_paths):
            add(name, issue)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            add(name, 'symlink-or-outside-root')
            continue
        if not path.is_file():
            add(name, 'missing-file')
            continue
        binary_content = name in content_paths and path.suffix in {'.sqlite3', '.gz', '.bin', '.part'}
        if binary_content and not deep:
            continue
        if deep and path.suffix == '.sqlite3':
            print('Scanning database pages: ' + name, file=sys.stderr, flush=True)
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rb') as stream:
            for issue in scan_stream(stream):
                add(name, issue)
        scanned += 1
    for name in content_paths - set(files):
        add(name, 'content-not-in-git-candidates')
    if policy['emptyGameTables']:
        if not (root / CONTENT / 'game_content.sqlite3').is_file():
            add(CONTENT + 'game_content.sqlite3', 'hydrate-content-before-private-state-check')
        else:
            for issue in check_private_tables(root / CONTENT / 'game_content.sqlite3', policy['emptyGameTables']):
                add(CONTENT + 'game_content.sqlite3', issue)
    for name in ('protocol.sqlite3', 'protocol_legacy.sqlite3'):
        if CONTENT + name not in logical_paths:
            continue
        if not (root / CONTENT / name).is_file():
            add(CONTENT + name, 'missing-protocol-content')
            continue
        for issue in check_protocol(root / CONTENT / name):
            add(CONTENT + name, issue)
        fields = json.loads((root / 'manifests/protocol-identity-fields.json').read_text())['fields']
        for issue in check_protocol_identity_fields(root / CONTENT / name, fields):
            add(CONTENT + name, issue)
    # Also inspect complete materialized files in deep mode, so an identifier
    # spanning two LFS objects cannot evade the raw database scan.
    if deep:
        for name in sorted(packed_paths):
            path = root / name
            if not path.is_file():
                add(name, 'hydrate-content-before-deep-check')
                continue
            print('Scanning reassembled database: ' + name, file=sys.stderr, flush=True)
            with path.open('rb') as stream:
                for issue in scan_stream(stream):
                    add(name, issue)
    return {'status': 'findings' if findings else 'checks-passed', 'candidateFiles': len(files),
            'scannedFiles': scanned, 'deep': deep, 'emptyTablesChecked': len(policy['emptyGameTables']),
            'findings': findings, 'limitations': [
                'Opaque packet fields are not semantically certified by string or sentinel checks.',
                'Raw database pages and decompressed pages are scanned only with --deep.',
                'This does not audit deployment vulnerabilities, dependencies, Git author identity or distribution rights.',
                'Publish Git candidates only, never an archive of the working directory.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deep', action='store_true')
    parser.add_argument('--repository', type=Path, default=ROOT, help='Repository to check; defaults to this checkout')
    args = parser.parse_args()
    result = audit(args.repository.resolve(), args.deep)
    print(json.dumps(result, indent=2))
    return 1 if result['findings'] else 0


if __name__ == '__main__':
    sys.exit(main())
