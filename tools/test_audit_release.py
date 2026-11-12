#!/usr/bin/env python3
import json
import struct
from pathlib import Path
import tempfile
import unittest
from audit_release import audit, portable_documents


def symbol_fixture(parts):
    blobs = bytearray(b'\0')
    def add(value):
        index = len(blobs)
        assert len(value) < 128 and index < 128
        blobs.extend(bytes([len(value)]) + value)
        return index
    indices = bytes(add(p.encode()) for p in parts)
    name = add(b'/' + indices)
    tables = struct.pack('<IBBBBQQIHHHH', 0, 2, 0, 0, 1, 1 << 48, 0, 1, name, 0, 0, 0)
    # Metadata root, two stream headers, then the table and blob heaps.
    header = b'BSJB' + struct.pack('<HHII', 1, 1, 0, 4) + b'v1\0\0' + struct.pack('<HH', 0, 2)
    offset = len(header) + 12 + 16
    return (header + struct.pack('<II', offset, len(tables)) + b'#~\0\0' +
            struct.pack('<II', offset + len(tables), len(blobs)) + b'#Blob\0\0\0' + tables + blobs)


class ReleaseChecks(unittest.TestCase):
    def test_segmented_symbol_path_cannot_bypass_raw_scanning(self):
        data = symbol_fixture(['', 'home', 'fixture-builder', 'code.cs'])
        self.assertNotIn(b'/home' + b'/fixture-builder', data)
        self.assertEqual(portable_documents(data), ['/home' + '/fixture-builder/code.cs'])
        (self.root / 'Core.pdb').write_bytes(data)
        self.assertIn('non-normalized-symbol-document', {r['issue'] for r in audit(self.root)['findings']})
        (self.root / 'Core.pdb').write_bytes(symbol_fixture(['', '_', 'server', 'code.cs']))
        self.assertFalse(audit(self.root)['findings'])

    def test_truncated_symbols_fail_closed(self):
        (self.root / 'Core.pdb').write_bytes(symbol_fixture(['', '_', 'server', 'code.cs'])[:-3])
        self.assertIn('unreadable-portable-symbols', {r['issue'] for r in audit(self.root)['findings']})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_source_link_reproduces_real_git_checkout_leak(self):
        document = '/home' + '/fixture-builder/repository/*'
        payload = json.dumps({'documents': {document: 'https://example.invalid/source/*'}}, separators=(',', ':')).encode()
        (self.root / 'AuthHost.pdb').write_bytes(b'BSJB' + payload)
        findings = {row['issue'] for row in audit(self.root)['findings']}
        self.assertIn('personal-home-path', findings)
        self.assertIn('automatic-source-link-map', findings)

    def test_client_release_excludes_server_code(self):
        (self.root / 'LauncherDesktop.dll').write_bytes(b'synthetic client')
        self.assertEqual(audit(self.root, True)['findings'], [])
        (self.root / 'Persistence.dll').write_bytes(b'synthetic server')
        self.assertIn('server-assembly-in-client-release', {row['issue'] for row in audit(self.root, True)['findings']})

    def test_utf16_builder_path_is_rejected(self):
        (self.root / 'Core.dll').write_bytes(('/home' + '/fixture-builder/code/').encode('utf-16le'))
        self.assertIn('personal-home-path', {row['issue'] for row in audit(self.root)['findings']})

    def test_no_assemblies_is_not_a_pass(self):
        self.assertEqual(audit(self.root)['status'], 'findings')


if __name__ == '__main__':
    unittest.main()
