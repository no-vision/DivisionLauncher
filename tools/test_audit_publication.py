#!/usr/bin/env python3
"""Regression checks for publication leaks, including ignored and staged files."""
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from audit_publication import audit, candidates, check_private_tables, check_protocol, check_protocol_identity_fields, path_issues, scan_stream


class PublicationChecks(unittest.TestCase):
    def test_encoded_and_bare_operator_paths(self):
        values = [b'/home' + b'/fixture-builder', b'/Users' + b'/fixture-builder',
                  b'C:' + b'\\users\\fixture-builder', b'C:' + b'\\\\Users\\\\fixture-builder',
                  b'D:' + b'/Documents and Settings/fixture-builder',
                  b'\\/home' + b'\\/fixture-builder\\/source',
                  b'%2Fhome' + b'%2Ffixture-builder',
                  b'C:%5CUsers' + b'%5Cfixture-builder']
        for value in values:
            for encoding in ('ascii', 'utf-16le', 'utf-16be'):
                with self.subTest(value=value, encoding=encoding):
                    self.assertIn('personal-home-path', scan_stream(io.BytesIO(value.decode().encode(encoding))))
        self.assertIn('machine-storage-path', scan_stream(io.BytesIO(b'/mnt' + b'/fixture-drive/project')))

    def test_client_only_repository_rejects_extra_server_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'manifests').mkdir()
            (root / 'manifests/content.json').write_text('{"files": []}')
            (root / 'manifests/publication-policy.json').write_text(json.dumps(dict(emptyGameTables=[],
                forbiddenPaths=['src/AuthHost/'], clientCorePath='src/Core/', allowedClientCoreFiles=['AccountIdentity.cs'])))
            self.assertEqual(audit(root)['findings'], [])
            extra = root / 'src/Core/Gameplay.cs'
            extra.parent.mkdir(parents=True)
            extra.write_text('// Private gameplay implementation')
            host = root / 'src/AuthHost/Program.cs'
            host.parent.mkdir(parents=True)
            host.write_text('// Private host implementation')
            issues = {row['issue'] for row in audit(root)['findings']}
            self.assertIn('non-client-core-source', issues)
            self.assertIn('private-source-outside-repository-scope', issues)

    def test_private_paths_and_only_manifest_databases_allowed(self):
        allowed = {'Production/server/content/game_content.sqlite3'}
        for name in ('runtime/account.txt', 'private-game/game.dat', '.env.production', 'save.sqlite3', 'auth.connection', 'test.pcapng', 'game.sqlite3-wal', '../outside.txt', 'server.dll', 'backup.tar.gz'):
            self.assertTrue(path_issues(name, allowed), name)
        self.assertFalse(path_issues(next(iter(allowed)), allowed))
        self.assertTrue(path_issues('Production/server/content/extra.sqlite3', allowed))
        self.assertTrue(path_issues('content-packs/orphan.part', allowed))

    def test_secret_patterns_and_utf16(self):
        values = [b'AK' + b'IA' + b'B' * 16, b'gh' + b'p_' + b'x' * 36,
                  b'Password' + b'=not-a-real-secret;',
                  b'https:' + b'//user:fixture-secret@host.invalid',
                  b'-----BEGIN ' + b'EC PRIVATE KEY-----\n' + b'A' * 40,
                  b'/home' + b'/fixture-person/project/']
        for value in values:
            self.assertTrue(scan_stream(io.BytesIO(value)))
            self.assertTrue(scan_stream(io.BytesIO(value.decode().encode('utf-16le'))))
            self.assertTrue(scan_stream(io.BytesIO(value.decode().encode('utf-16be'))))

    def test_secret_across_chunk_boundary(self):
        value = b'AK' + b'IA' + b'B' * 16
        self.assertIn('cloud-access-key', scan_stream(io.BytesIO(b' ' * (4 * 1024 * 1024 - 5) + value)))

    def test_placeholder_key_is_not_a_private_key(self):
        self.assertFalse(scan_stream(io.BytesIO(b'-----BEGIN ' + b'EC PRIVATE KEY-----\nfixture')))

    def test_asset_names_and_unrelated_binary_bytes_are_not_credentials(self):
        self.assertFalse(scan_stream(io.BytesIO(b'player_thighs_start' + b'0' * 40)))
        url = b'https:' + b'//user:fixture-secret@host.invalid'
        self.assertFalse(scan_stream(io.BytesIO(b'\0\0'.join(bytes([c]) for c in url))))

    def test_forced_tracked_ignored_files_are_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_text('*.connection\n')
            (root / 'secret.connection').write_text('fixture')
            self.assertNotIn('secret.connection', candidates(root))
            subprocess.run(['git', '-C', str(root), 'add', '-f', 'secret.connection'], check=True)
            self.assertIn('secret.connection', candidates(root))

    def test_private_rows_fail_and_database_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'content.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE PlayerAccount(Name TEXT)')
            self.assertEqual(check_private_tables(path, ['PlayerAccount']), [])
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO PlayerAccount VALUES ('fixture')")
            before = path.read_bytes()
            self.assertEqual(check_private_tables(path, ['PlayerAccount']), ['private-state:PlayerAccount'])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(check_private_tables(path, ['Missing']), ['missing-private-table:Missing'])

    def test_non_synthetic_protocol_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'protocol.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript("CREATE TABLE packet(id INTEGER,payload BLOB,bundle TEXT DEFAULT 'lobby.initial'); CREATE TABLE patch(packet INTEGER,offset INTEGER,width INTEGER,kind TEXT);")
                db.execute('INSERT INTO packet(id,payload) VALUES (1,?)', (b'fixture-person',))
                db.execute("INSERT INTO patch VALUES (1,0,14,'Username')")
            self.assertIn('non-synthetic-protocol-field:Username', check_protocol(path))
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO packet(id,payload) VALUES (2,?)', (b'12345678-' + b'1234-1234-' + b'1234567812345678',))
            self.assertIn('captured-service-process-identifier', check_protocol(path))

    def test_profile_and_dictionary_identity_policy(self):
        synthetic = bytes.fromhex('facefeed000000000000000000000001')
        fields = [dict(table='blob', name='profile.draft', offset=0, width=16, expectedHex=synthetic.hex()),
                  dict(table='reference', mode='creation', token=2, expectedHex=synthetic.hex()),
                  dict(table='packet', packet=1, message=333, bundle='creation.initial', ordinal=0,
                       offset=1, width=16, expectedHex=synthetic.hex())]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'protocol.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE blob(name TEXT,data BLOB); CREATE TABLE reference(mode TEXT,token INTEGER,identity TEXT);'
                    'CREATE TABLE packet(id INTEGER,payload BLOB,message INTEGER,bundle TEXT,ordinal INTEGER);')
                db.execute('INSERT INTO blob VALUES(?,?)', ('profile.draft', synthetic))
                db.execute('INSERT INTO reference VALUES(?,?,?)', ('creation', 2, synthetic.hex()))
                db.execute('INSERT INTO packet VALUES(1,?,333,?,0)', (b'!' + synthetic, 'creation.initial'))
            before = path.read_bytes()
            self.assertFalse(check_protocol_identity_fields(path, fields))
            self.assertEqual(before, path.read_bytes())
            with sqlite3.connect(path) as db:
                db.execute('UPDATE blob SET data=?', (b'X' * 16,))
                db.execute('UPDATE reference SET identity=?', ((b'Y' * 16).hex(),))
                db.execute('UPDATE packet SET message=7')
            self.assertEqual(len(check_protocol_identity_fields(path, fields)), 3)


if __name__ == '__main__':
    unittest.main()
