#!/usr/bin/env python3
"""Check first-party published assemblies for builder paths and source maps."""
import argparse
import io
import json
import struct
from pathlib import Path
from audit_publication import scan_stream

FIRST_PARTY = {'Core', 'Content', 'Persistence', 'Retail', 'Launcher', 'LauncherDesktop',
               'AuthHost', 'LobbyHost', 'WorldHost', 'ServerAdmin', 'ContentPublisher'}
SERVER_ONLY = {'Content', 'Persistence', 'Retail', 'AuthHost', 'LobbyHost', 'WorldHost', 'ServerAdmin', 'ContentPublisher'}


def portable_documents(data):
    """Decode segmented Document names that a raw byte search cannot see.

    Portable PDBs store each path component separately in the blob heap.
    Reject malformed/unsupported symbols instead of silently skipping them.
    """
    def uint(offset, size):
        if offset < 0 or offset + size > len(data):
            raise ValueError('truncated metadata')
        return int.from_bytes(data[offset:offset + size], 'little')

    if data[:4] != b'BSJB':
        raise ValueError('not a portable PDB')
    cursor = (16 + uint(12, 4) + 3) & ~3
    streams = uint(cursor + 2, 2)
    cursor += 4
    heaps = {}
    for _ in range(streams):
        start, size = uint(cursor, 4), uint(cursor + 4, 4)
        end = data.index(b'\0', cursor + 8)
        name = data[cursor + 8:end].decode('ascii')
        cursor = (end + 4) & ~3
        if start + size > len(data):
            raise ValueError('truncated stream')
        heaps[name] = data[start:start + size]
    tables, blobs = heaps['#~'], heaps['#Blob']
    flags = tables[6]
    valid = int.from_bytes(tables[8:16], 'little')
    cursor, counts = 24, {}
    for table in range(64):
        if valid & (1 << table):
            counts[table] = struct.unpack_from('<I', tables, cursor)[0]
            cursor += 4
    if any(counts.get(t, 0) for t in range(48)):
        raise ValueError('unexpected non-debug tables in portable PDB')
    blob_width, guid_width = (4 if flags & 4 else 2), (4 if flags & 2 else 2)

    def compressed(buffer, offset):
        first = buffer[offset]
        if first < 0x80:
            return first, offset + 1
        width = 2 if first < 0xc0 else 4 if first < 0xe0 else 0
        if not width or offset + width > len(buffer):
            raise ValueError('invalid compressed integer')
        value = int.from_bytes(buffer[offset:offset + width], 'big')
        return value & (0x3fff if width == 2 else 0x1fffffff), offset + width

    def blob(offset):
        size, start = compressed(blobs, offset)
        if start + size > len(blobs):
            raise ValueError('truncated blob')
        return blobs[start:start + size]

    names = []
    for _ in range(counts.get(48, 0)):
        if cursor + 2 * (blob_width + guid_width) > len(tables):
            raise ValueError('truncated Document table')
        index = int.from_bytes(tables[cursor:cursor + blob_width], 'little')
        cursor += 2 * (blob_width + guid_width)
        name = blob(index)
        separator, position, parts = chr(name[0]), 1, []
        while position < len(name):
            part, position = compressed(name, position)
            parts.append(blob(part).decode('utf-8'))
        names.append(separator.join(parts))
    return names


def audit(directory, client_only=False):
    findings, checked, names = [], 0, set()
    for path in sorted(directory.rglob('*')):
        if path.suffix not in ('.dll', '.pdb') or path.stem not in FIRST_PARTY:
            continue
        name = path.relative_to(directory).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            findings.append(dict(path=name, issue='assembly-symlink'))
            continue
        data = path.read_bytes()
        checked += 1
        names.add(path.name)
        for issue in scan_stream(io.BytesIO(data)):
            findings.append(dict(path=name, issue=issue))
        if b'"documents":{' in data:
            findings.append(dict(path=name, issue='automatic-source-link-map'))
        if client_only and path.stem in SERVER_ONLY:
            findings.append(dict(path=name, issue='server-assembly-in-client-release'))
        if path.name == 'LauncherDesktop.pdb':
            findings.append(dict(path=name, issue='gui-release-symbols'))
        if path.suffix == '.pdb':
            try:
                documents = portable_documents(data)
                if any(not p.startswith(('/_/server/', '/_/launcher/')) for p in documents):
                    findings.append(dict(path=name, issue='non-normalized-symbol-document'))
            except (ValueError, KeyError, IndexError, UnicodeError, struct.error):
                findings.append(dict(path=name, issue='unreadable-portable-symbols'))
    if not checked:
        findings.append(dict(path='.', issue='no-first-party-assemblies'))
    if client_only and 'LauncherDesktop.dll' not in names:
        findings.append(dict(path='.', issue='missing-launcher'))
    return dict(status='findings' if findings else 'checks-passed', assembliesChecked=checked, findings=findings,
                limitation='Known path/secret patterns, SourceLink maps and portable PDB Document paths; third-party binaries and compressed embedded sources require separate review.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--client-only', action='store_true')
    args = parser.parse_args()
    result = audit(args.directory.resolve(), args.client_only)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['findings']))


if __name__ == '__main__':
    main()
