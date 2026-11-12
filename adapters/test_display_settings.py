import tempfile
import unittest
from pathlib import Path
from display_settings import apply_display


class DisplaySettingsTests(unittest.TestCase):
    def test_isolated_clients_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, mode, width, height in [('one', 'windowed', 1280, 720), ('two', 'fullscreen', 1920, 1080)]:
                run = root / name
                documents = run / 'documents'
                apply_display(run, documents, mode, width, height)
                text = (documents / 'state.cfg').read_text()
                self.assertIn(f'width = {width}', text)
                self.assertIn(f'height = {height}', text)
                self.assertIn('windowed = ' + ('true' if mode == 'windowed' else 'false'), text)
            self.assertIn('1280', (root / 'one/documents/state.cfg').read_text())

    def test_default_and_existing_settings_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            apply_display(run, run / 'documents', 'default', 1280, 720)
            self.assertFalse((run / 'documents').exists())
            documents = run / 'documents'; documents.mkdir()
            settings = documents / 'state.cfg'
            original = '{ graphics = { dx12 = false, }, input = { sensitivity = 5, }, }'
            settings.write_text(original)
            with self.assertRaises(ValueError): apply_display(run, documents, 'windowed', 1280, 720)
            self.assertEqual(original, settings.read_text())
            with self.assertRaises(ValueError): apply_display(run, run.parent, 'windowed', 1280, 720)
            with self.assertRaises(ValueError): apply_display(run, documents, 'windowed', 0, 720)


if __name__ == '__main__': unittest.main()
