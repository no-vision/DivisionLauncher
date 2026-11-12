"""Bounded snapshot transport for the in-game diagnostic panels.

Text rasterization and input handling run outside the game's Present callback.
The native renderer receives only bounded RGBA panels, never raw packet data.
"""
from pathlib import Path
import time


class OverlayFeed:
    def __init__(self, Snapshot: Path):
        import json
        from overlay_ui import OverlayUi
        self.Snapshot = Snapshot
        self.Modified = None
        self.Stale = False
        Roles = json.loads((Path(__file__).parent / 'overlay/packet_roles.json').read_text())
        self.Ui = OverlayUi(Snapshot.parents[2] / 'profiles/overlay-layout.json', Roles)

    def Next(self, Input=None):
        import json
        self.Ui.Input(Input or {})
        try:
            Stat = self.Snapshot.stat()
            if Stat.st_mtime_ns != self.Modified:
                with self.Snapshot.open('rb') as Source:
                    Raw = Source.read(2 * 1024 * 1024 + 1)
                if len(Raw) > 2 * 1024 * 1024:
                    raise ValueError('oversized_overlay_snapshot')
                self.Ui.Ingest(json.loads(Raw))
                self.Modified = Stat.st_mtime_ns
                self.Stale = False
            if time.time() - Stat.st_mtime > 3 and not self.Stale:
                self.Ui.Pose = 'POS / FEED STALE'
                self.Ui.Status = 'Feed stale; retained packets remain inspectable'
                self.Ui.Dirty, self.Stale = True, True
        except (OSError, ValueError, UnicodeError, KeyError, TypeError):
            if not self.Stale:
                self.Ui.Pose = 'POS / WAITING FOR FEED'
                self.Ui.Status = 'Waiting for private telemetry'
                self.Ui.Dirty, self.Stale = True, True
        return self.Ui.Render()
