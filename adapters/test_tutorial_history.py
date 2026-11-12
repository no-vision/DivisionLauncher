"""Launcher UI-state persistence: scope, grant rotation, atomic save and failures."""
from pathlib import Path
import tempfile
import unittest
import uuid

from tutorial_history import TutorialHistory


class TutorialHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1]/'.cache/tests'
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.root = Path(self.temporary.name)
        self.realm, self.account = str(uuid.uuid4()), str(uuid.uuid4())
        self.data = b'\x01\0\0\0' + b'private tutorial state'

    def tearDown(self):
        self.temporary.cleanup()

    def history(self, run, grant, realm=None, account=None):
        return TutorialHistory(self.root/'cache', self.root/run,
            realm or self.realm, account or self.account, grant * 24)

    def test_grant_rotation_restores_exact_native_file(self):
        first = self.history('first', 'a')
        self.assertFalse(first.restore())
        first.write(first.current, self.data)
        self.assertFalse(first.save())  # still being observed for stability
        self.assertTrue(first.save())
        second = self.history('second', 'b')
        self.assertTrue(second.restore())
        self.assertEqual(second.current.read_bytes(), self.data)
        self.assertNotEqual(first.current.name, second.current.name)
        self.assertEqual(first.cached, second.cached)
        self.assertFalse(second.save())
        self.assertNotIn('a' * 24, str(second.cached))

    def test_realm_and_account_history_are_separate(self):
        first = self.history('first', 'a')
        first.write(first.current, self.data)
        first.save(final=True)
        self.assertFalse(self.history('other-account', 'b', account=str(uuid.uuid4())).restore())
        self.assertFalse(self.history('other-realm', 'c', realm=str(uuid.uuid4())).restore())

    def test_missing_or_unsupported_history_preserves_previous_file(self):
        history = self.history('first', 'a')
        history.write(history.current, self.data)
        history.save(final=True)
        for invalid in (b'\x01', b'\x02\0\0\0' + b'unknown version', b'\x01\0\0\0' + b'x' * history.MaximumBytes):
            history.current.write_bytes(invalid)
            with self.assertRaises(ValueError): history.save(final=True)
            self.assertEqual(history.cached.read_bytes(), self.data)
        history.current.unlink()
        self.assertFalse(history.save(final=True))
        self.assertEqual(history.cached.read_bytes(), self.data)

    def test_restore_does_not_overwrite_running_client_history(self):
        history = self.history('first', 'a')
        history.write(history.current, self.data)
        history.save(final=True)
        with self.assertRaises(ValueError): history.restore()

    def test_documents_symlink_cannot_import_another_profile(self):
        history = self.history('first', 'a')
        documents = history.run/'compat/pfx/drive_c/users/steamuser/Documents'
        documents.parent.mkdir(parents=True)
        unrelated = self.root/'unrelated'; unrelated.mkdir()
        documents.symlink_to(unrelated, target_is_directory=True)
        with self.assertRaises(ValueError): history.restore()
        with self.assertRaises(ValueError): history.save(final=True)


if __name__ == '__main__':
    unittest.main()
