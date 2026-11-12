"""Apply explicit launcher display preferences only inside an isolated game run.

state.cfg graphics/windowedSettings/fullscreenSettings names come from files
written by this client. No retail settings, account state or game files copied.
"""
from pathlib import Path
import re


def add_arguments(parser):
    parser.add_argument('--display-mode', choices=('default', 'windowed', 'fullscreen'), default='default')
    parser.add_argument('--display-width', type=int, default=1280)
    parser.add_argument('--display-height', type=int, default=720)


def apply_display(run: Path, documents: Path, mode: str, width: int, height: int):
    if mode not in ('default', 'windowed', 'fullscreen') or not 640 <= width <= 16384 or not 480 <= height <= 16384:
        raise ValueError('Invalid display preference')
    if mode == 'default':
        return
    destination = documents / 'state.cfg'
    if not destination.resolve().is_relative_to(run.resolve()):
        raise ValueError('Display settings must remain inside the private run')
    # A fresh private run has no state.cfg. Preserve unrelated fields if one is
    # already present; refuse unrecognized input instead of overwriting it.
    content = destination.read_text(encoding='utf-8') if destination.exists() else '{\n}\n'
    if len(content) > 1024 * 1024:
        raise ValueError('Oversized game settings')
    windowed = 'true' if mode == 'windowed' else 'false'
    graphics = '\tgraphics = {\n\t\tinitsettingsfromhardware = false,\n\t\twindowed = ' + windowed + ',\n'
    for key, fullscreen in [('windowedSettings', 'false'), ('fullscreenSettings', 'true')]:
        graphics += (f'\t\t{key} = {{\n\t\t\tfullscreen = {fullscreen},\n'
                     f'\t\t\twidth = {width},\n\t\t\theight = {height},\n'
                     '\t\t\tmaximized = false,\n\t\t},\n')
    graphics += '\t},'
    # Adapters call this once before spawning the game. Never replace a complete
    # existing graphics block, which may hold GPU-specific preferences.
    if re.search(r'\bgraphics\s*=', content):
        raise ValueError('Display settings already initialized for this run')
    end = content.rfind('}')
    if end < 0:
        raise ValueError('Malformed game settings')
    content = content[:end] + graphics + '\n' + content[end:]
    documents.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(destination)
