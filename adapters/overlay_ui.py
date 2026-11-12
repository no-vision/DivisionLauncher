"""Read-only in-game diagnostic UI. No game pointers, packet mutation or account state.
Input and rendering stay outside Present. Bounded history; selected evidence is pinned.
"""
from collections import OrderedDict
from pathlib import Path
import base64
import json
import math
import re
import time
from PIL import Image, ImageDraw, ImageFont

MAX_WIDTH, MAX_HEIGHT = 1200, 900
MAX_EVENTS, MAX_BYTES = 512, 8 * 1024 * 1024


def PacketIds(Text):
    Text = Text.strip()
    if not Text or Text.lower() in ('all', '*'):
        return None
    Parts = re.split(r'[,;\s]+', Text)
    if len(Parts) > 128 or any(not re.fullmatch(r'(?:0x[0-9a-fA-F]+|\d+)', Part) for Part in Parts):
        raise ValueError('Use IDs separated by commas, or ALL')
    Ids = {int(Part, 16 if Part.lower().startswith('0x') else 10) for Part in Parts}
    if any(Value > 0xffffffff for Value in Ids):
        raise ValueError('Message ID out of range')
    return Ids


class Panel:
    def __init__(self, X, Y, Width, Height, Font=14, MinimumWidth=250, MinimumHeight=26, Header=False):
        self.X, self.Y, self.Width, self.Height = X, Y, Width, Height
        self.Font, self.Opacity = Font, .76
        self.MinimumWidth, self.MinimumHeight = MinimumWidth, MinimumHeight
        self.Header, self.Minimized, self.Closed = Header, False, False

    def Clamp(self, Screen):
        self.Width = max(self.MinimumWidth, min(MAX_WIDTH, Screen[0], int(self.Width)))
        self.Height = max(self.MinimumHeight, min(MAX_HEIGHT, Screen[1], int(self.Height)))
        self.X = max(0, min(Screen[0] - self.Width, int(self.X)))
        self.Y = max(0, min(Screen[1] - self.Height, int(self.Y)))
        self.Font = max(11, min(24, int(self.Font)))
        self.Opacity = max(.15, min(1., float(self.Opacity)))

    def Contains(self, X, Y):
        return self.X <= X < self.X + self.Width and self.Y <= Y < self.Y + self.Height


class OverlayUi:
    def __init__(self, Settings, Roles=None):
        self.Settings = Path(Settings)
        self.Panels = [Panel(16, 16, 900, 28), Panel(16, 58, 1000, 580, MinimumWidth=520, MinimumHeight=250, Header=True)]
        self.Screen = (1920, 1080)
        self.Enabled = [False, False]
        self.Pose, self.Status = 'POS / WAITING', 'Waiting for private observations'
        self.Events = OrderedDict()
        self.TotalBytes, self.Evicted, self.Cursor = 0, 0, 0
        self.Observed, self.Skipped = None, 0
        self.Selected, self.Ids, self.Filter = None, None, 'ALL'
        self.FilterEdit, self.Error = False, ''
        self.Direction, self.Paused, self.Frozen = 'BOTH', False, []
        self.ListScroll, self.HexScroll, self.Tab = 0, 0, 'LIST'
        self.Hits, self.Rows = [], []
        self.Drag, self.Down = None, None
        self.EditMode, self.Mouse, self.Dirty = False, (0, 0), True
        self.LastInput, self.LastSave = 0, 0
        self.Roles = Roles or {}
        try:
            Saved = json.loads(self.Settings.read_text())
            for PanelValue, Value in zip(self.Panels, Saved.get('panels', [])):
                for Name in ('X', 'Y', 'Width', 'Height', 'Font', 'Opacity'):
                    Number = Value.get(Name)
                    if isinstance(Number, (int, float)) and math.isfinite(Number):
                        setattr(PanelValue, Name, Number)
                for Name in ('Header', 'Minimized', 'Closed'):
                    if isinstance(Value.get(Name), bool):
                        setattr(PanelValue, Name, Value[Name])
            self.Filter = str(Saved.get('filter', 'ALL'))[:256]
            self.Ids = PacketIds(self.Filter)
        except (OSError, ValueError, TypeError, AttributeError):
            self.Filter, self.Ids = 'ALL', None
        for PanelValue in self.Panels:
            PanelValue.Clamp(self.Screen)

    def Save(self):
        self.Settings.parent.mkdir(parents=True, exist_ok=True)
        Temporary = self.Settings.with_suffix('.tmp')
        Temporary.write_text(json.dumps({'panels': [vars(Value) for Value in self.Panels], 'filter': self.Filter}, indent=2))
        Temporary.replace(self.Settings)
        self.LastSave = time.monotonic()

    def Role(self, Event):
        Key = f"{Event.get('service', '')}/{Event.get('direction', '')}/{Event.get('messageId', '')}"
        Value = self.Roles.get(Key)
        return Value['role'] if Value else 'Unknown role'

    def Ingest(self, Snapshot):
        if Snapshot.get('version') != 2:
            raise ValueError('Unsupported overlay feed version')
        self.Enabled = [bool(Snapshot.get('poseEnabled')), bool(Snapshot.get('packetsEnabled'))]
        self.Pose = str(Snapshot.get('pose', 'POS / WAITING'))[:256]
        self.Status = str(Snapshot.get('status', ''))[:300]
        self.Evicted = int(Snapshot.get('evicted', 0))
        Added = 0
        for Entry in Snapshot.get('events', [])[:128]:
            Index, Event = Entry['index'], Entry['event']
            if Index in self.Events or Event.get('kind') != 'packet':
                continue
            Raw = b'' if Event.get('redacted') else base64.b64decode(Event.get('message', ''), validate=True)
            if len(Raw) > 256 * 1024 + 5:
                continue
            Event = dict(Event, Raw=Raw, Index=Index)
            self.Events[Index] = Event
            Added += 1
            self.TotalBytes += len(Raw)
            self.Cursor = max(self.Cursor, Index)
        Observed = Snapshot.get('packetsObserved')
        if isinstance(Observed, int):
            if self.Observed is not None:
                self.Skipped += max(0, Observed - self.Observed - Added)
            self.Observed = Observed
        while len(self.Events) > MAX_EVENTS or self.TotalBytes > MAX_BYTES:
            _, Event = self.Events.popitem(last=False)
            self.TotalBytes -= len(Event['Raw'])
        self.Dirty = True

    def Matching(self):
        return [Event for Event in reversed(list(self.Events.values()))
                if (self.Ids is None or Event.get('messageId') in self.Ids)
                and (self.Direction == 'BOTH' or Event.get('direction') == self.Direction)]

    def Input(self, State):
        Screen = tuple(State.get('screen', self.Screen))
        if len(Screen) == 2 and 640 <= Screen[0] <= 16384 and 360 <= Screen[1] <= 16384 and Screen != self.Screen:
            self.Screen = Screen
            for Value in self.Panels:
                Value.Clamp(Screen)
            self.Dirty = True
        Edit = bool(State.get('edit'))
        SoftwareCursor = bool(State.get('softwareCursor', Edit))
        if SoftwareCursor != getattr(self, 'SoftwareCursor', False):
            self.Dirty = True
        self.SoftwareCursor = SoftwareCursor
        Mouse = tuple(State.get('mouse', self.Mouse))
        if Edit != self.EditMode or Edit and Mouse != self.Mouse:
            self.Dirty = True
        self.EditMode, self.Mouse = Edit, Mouse
        for Event in State.get('events', [])[:64]:
            Kind, X, Y, Value, Flags = Event
            self.Handle(Kind, X, Y, Value, Flags)
        if not Edit and self.Drag:
            self.Drag, self.Down = None, None
            self.Save()

    def PanelFrame(self, Index):
        PanelValue = self.Panels[Index]
        if not self.Enabled[Index] or PanelValue.Closed and not self.EditMode:
            return 0, 0, False
        HeaderHeight = PanelValue.Font + 12 if PanelValue.Header or self.EditMode or PanelValue.Minimized or PanelValue.Closed else 0
        Collapsed = PanelValue.Minimized or PanelValue.Closed
        Height = HeaderHeight if Collapsed else (max(PanelValue.Height, PanelValue.Font + 8) + HeaderHeight if Index == 0 else PanelValue.Height)
        return min(Height, self.Screen[1] - PanelValue.Y, MAX_HEIGHT), HeaderHeight, Collapsed

    def PanelAt(self, X, Y):
        for Index in (1, 0):
            PanelValue = self.Panels[Index]
            Height, _, _ = self.PanelFrame(Index)
            if Height and PanelValue.X <= X < PanelValue.X + PanelValue.Width and PanelValue.Y <= Y < PanelValue.Y + Height:
                return Index
        return None

    def Handle(self, Kind, X, Y, Value, Flags):
        self.Dirty = True
        if Kind == 4:  # WM_CHAR, only emitted while overlay controls own input.
            if not self.FilterEdit:
                return
            if Value == 13:
                try:
                    self.Ids = PacketIds(self.Filter)
                    self.FilterEdit, self.Error, self.ListScroll = False, '', 0
                    self.Save()
                except ValueError as Error:
                    self.Error = str(Error)
            elif Value == 27:
                self.FilterEdit = False
            elif Value == 8:
                self.Filter = self.Filter[:-1]
            elif chr(Value) in '0123456789abcdefABCDEFxX,; *Ll' and len(self.Filter) < 256:
                self.Filter += chr(Value)
            return
        PanelIndex = self.PanelAt(X, Y)
        if Kind == 0 and self.Drag:
            Index, Action, StartX, StartY, Original = self.Drag
            PanelValue = self.Panels[Index]
            if Action == 'resize':
                PanelValue.Width = Original[2] + X - StartX
                PanelValue.Height = Original[3] + Y - StartY
                if Index == 1:
                    PanelValue.Width, PanelValue.Height = max(380, PanelValue.Width), max(260, PanelValue.Height)
            else:
                PanelValue.X, PanelValue.Y = Original[0] + X - StartX, Original[1] + Y - StartY
            PanelValue.Clamp(self.Screen)
            return
        if Kind == 1 and PanelIndex is not None:
            PanelValue = self.Panels[PanelIndex]
            Height, HeaderHeight, Collapsed = self.PanelFrame(PanelIndex)
            ToolbarButton = any(isinstance(Action, tuple) and Rect[0] <= X < Rect[2] and Rect[1] <= Y < Rect[3] for Rect, Action in self.Hits)
            Resize = not Collapsed and X >= PanelValue.X + PanelValue.Width - 18 and Y >= PanelValue.Y + Height - 18
            Move = Flags & 1 or Y < PanelValue.Y + HeaderHeight or PanelIndex == 0
            if ToolbarButton:
                Resize = Move = False
            self.Down = (X, Y, PanelIndex)
            if Resize or Move:
                self.Drag = (PanelIndex, 'resize' if Resize else 'move', X, Y,
                             (PanelValue.X, PanelValue.Y, PanelValue.Width, PanelValue.Height))
        elif Kind == 2:
            Down = self.Down
            self.Down, self.Drag = None, None
            if Down and abs(X - Down[0]) + abs(Y - Down[1]) < 5:
                self.Click(X, Y)
            self.Save()
        elif Kind == 3 and PanelIndex is not None:
            PanelValue = self.Panels[PanelIndex]
            if Flags & 2:  # Control-wheel: text scale.
                PanelValue.Font += 1 if Value > 0 else -1
                PanelValue.Clamp(self.Screen)
                self.Save()
            elif Flags & 4:  # Alt-wheel: background transparency.
                PanelValue.Opacity += .05 if Value > 0 else -.05
                PanelValue.Clamp(self.Screen)
                self.Save()
            elif PanelIndex != 1 or self.PanelFrame(PanelIndex)[2]:
                return
            elif self.Tab == 'HEX':
                self.HexScroll = max(0, self.HexScroll + (-3 if Value > 0 else 3))
            else:
                # Freeze the viewed rows while inspecting history; incoming
                # packets remain recorded. Follow latest explicitly resumes.
                if not self.Paused:
                    self.Frozen, self.Paused = self.Matching(), True
                self.ListScroll = max(0, self.ListScroll + (-3 if Value > 0 else 3))

    def Click(self, X, Y):
        for Rect, Action in reversed(self.Hits):
            if Rect[0] <= X < Rect[2] and Rect[1] <= Y < Rect[3]:
                self.Dirty = True
                if isinstance(Action, tuple):
                    Command, Index = Action
                    PanelValue = self.Panels[Index]
                    if Command == 'header':
                        PanelValue.Header = not PanelValue.Header
                    elif Command == 'minimize':
                        PanelValue.Minimized = not PanelValue.Minimized
                    elif Command == 'close':
                        PanelValue.Closed = True
                        if Index == 1:
                            self.FilterEdit = False
                    elif Command == 'show':
                        PanelValue.Closed = PanelValue.Minimized = False
                    self.Save()
                elif isinstance(Action, dict):
                    self.Selected, self.HexScroll, self.Tab = Action, 0, 'HEX'
                elif Action == 'follow':
                    self.Paused, self.Frozen, self.ListScroll, self.Tab = False, [], 0, 'LIST'
                elif Action == 'filter':
                    self.FilterEdit, self.Filter, self.Error = True, '', ''
                elif Action == 'pause':
                    self.Paused = not self.Paused
                    if self.Paused:
                        self.Frozen = self.Matching()
                    else:
                        self.ListScroll = 0
                elif Action == 'direction':
                    self.Direction = {'BOTH': 'C2S', 'C2S': 'S2C', 'S2C': 'BOTH'}[self.Direction]
                    self.ListScroll = 0
                elif Action in ('LIST', 'HEX'):
                    self.Tab = Action
                elif Action in ('font-', 'font+', 'alpha-', 'alpha+'):
                    PanelValue = self.Panels[1]
                    if Action.startswith('font'):
                        PanelValue.Font += 1 if Action.endswith('+') else -1
                    else:
                        PanelValue.Opacity += .05 if Action.endswith('+') else -.05
                    PanelValue.Clamp(self.Screen)
                return

    def Render(self):
        if not self.Dirty:
            return []
        self.Dirty = False
        self.Hits = []
        Frames = []
        for Index, PanelValue in enumerate(self.Panels):
            PanelValue.Clamp(self.Screen)
            Height, HeaderHeight, Collapsed = self.PanelFrame(Index)
            if not Height:
                Frames.append((Index, 0, 0, 0, 0, b''))
                continue
            ImageValue = Image.new('RGBA', (PanelValue.Width, Height), (15, 21, 29, round(255 * PanelValue.Opacity)))
            Draw = ImageDraw.Draw(ImageValue)
            Font = ImageFont.load_default(size=PanelValue.Font)
            Mono = PanelValue.Font * .65  # Hex is drawn into fixed-width cells.
            Line = PanelValue.Font + 6
            def Text(X, Y, Value, Color=(224, 233, 242, 255), Width=None):
                Width = PanelValue.Width - X - 6 if Width is None else Width
                Value = str(Value).replace('\n', ' ')
                while Value and Draw.textlength(Value, font=Font) > Width:
                    Value = Value[:-1]
                Draw.text((X, Y), Value, font=Font, fill=Color)
            def Button(X, Y, Width, Label, Action):
                Draw.rectangle((X, Y, X + Width - 3, Y + Line), fill=(39, 52, 68, 245))
                Text(X + 5, Y + 2, Label, Width=Width - 12)
                self.Hits.append(((PanelValue.X + X, PanelValue.Y + Y, PanelValue.X + X + Width - 3,
                                   PanelValue.Y + Y + Line), Action))
            Orange = (255, 181, 91, 255)
            if HeaderHeight:
                Draw.rectangle((0, 0, PanelValue.Width, HeaderHeight - 1), fill=(30, 40, 54, round(255 * PanelValue.Opacity)))
                Title = 'Position' if Index == 0 else 'Packets'
                if PanelValue.Closed:
                    Text(7, 4, Title + ' — closed', Orange, Width=PanelValue.Width - 98)
                    Button(PanelValue.Width - 86, 3, 80, 'Show', ('show', Index))
                else:
                    Size = max(28, PanelValue.Font + 14)
                    ToggleWidth = max(72, PanelValue.Font * 5)
                    Start = PanelValue.Width - Size * 2 - ToggleWidth - 12
                    Text(7, 4, Title, Orange, Width=Start - 12)
                    Button(Start, 3, ToggleWidth, 'Bar on' if PanelValue.Header else 'Bar off', ('header', Index))
                    Button(Start + ToggleWidth + 2, 3, Size, '+' if PanelValue.Minimized else '-', ('minimize', Index))
                    Button(Start + ToggleWidth + Size + 4, 3, Size, 'X', ('close', Index))
            if Collapsed:
                Frames.append((Index, PanelValue.X, PanelValue.Y, PanelValue.Width, Height, ImageValue.tobytes()))
                continue
            if Index == 0:
                Text(7, HeaderHeight + 3, self.Pose, Orange)
            else:
                Y = HeaderHeight + 4
                Button(8, Y, 58, 'List', 'LIST'); Button(70, Y, 58, 'Hex', 'HEX')
                Button(132, Y, 88, 'Resume' if self.Paused else 'Pause', 'pause')
                Button(224, Y, 64, self.Direction, 'direction')
                Button(292, Y, 38, 'A-', 'font-'); Button(334, Y, 38, 'A+', 'font+')
                Y += Line + 5
                Button(8, Y, max(180, PanelValue.Width - 166), ('IDs: ' + self.Filter + ('_' if self.FilterEdit else '')), 'filter')
                Button(PanelValue.Width - 150, Y, 68, 'Fade -', 'alpha-')
                Button(PanelValue.Width - 78, Y, 70, 'Fade +', 'alpha+')
                Y += Line + 6
                Button(8, Y, 144, 'Follow latest' if self.Paused or self.Tab == 'HEX' else 'Following latest', 'follow')
                Y += Line + 5
                Text(8, Y, self.Error or ('IDs: ALL, 18, or 18,31,32,438 | Enter applies | wheel scrolls'), Orange)
                Y += Line + 3
                Bottom = Height - Line - 10
                if self.Tab == 'LIST':
                    Rows = self.Frozen if self.Paused else self.Matching()
                    Count = max(0, (Bottom - Y) // (Line + 1))
                    self.ListScroll = min(self.ListScroll, max(0, len(Rows) - Count))
                    for Event in Rows[self.ListScroll:self.ListScroll + Count]:
                        Stamp = str(Event.get('time', ''))[11:19]
                        Label = f"{Stamp} {Event.get('direction', '')} #{Event.get('messageId')} {self.Role(Event)} | {Event.get('payloadBytes', 0)} B"
                        Button(8, Y, PanelValue.Width - 16, Label, Event)
                        Y += Line + 1
                elif self.Selected:
                    Event = self.Selected
                    Raw = Event['Raw']
                    IdBytes = Event.get('idBytes', 0)
                    End = IdBytes + Event.get('payloadBytes', 0)
                    for Label in (f"#{Event.get('messageId')} {Event.get('direction')} {self.Role(Event)}",
                                  f"{Event.get('service')} | ID [0,{IdBytes}) | Contents [{IdBytes},{End})",
                                  f"Retained {len(Raw)}/{End} B | {'REDACTED' if Event.get('redacted') else 'TRUNCATED' if Event.get('truncated') else 'Plaintext'} | payload fields undecoded"):
                        Text(8, Y, Label, Orange); Y += Line
                    Columns = next((Count for Count in (16, 8, 4, 1) if (12 + Count * 4) * Mono < PanelValue.Width - 16), 1)
                    Count = max(0, (Bottom - Y) // Line)
                    self.HexScroll = min(self.HexScroll, max(0, math.ceil(len(Raw) / Columns) - Count))
                    for Offset in range(self.HexScroll * Columns, min(len(Raw), (self.HexScroll + Count) * Columns), Columns):
                        Text(8, Y, f'{Offset:08X}', Width=Mono * 9)
                        for Cell, Byte in enumerate(Raw[Offset:Offset + Columns]):
                            Color = Orange if Offset + Cell < IdBytes else (139, 208, 245, 255)
                            X = 8 + int((10 + Cell * 3) * Mono)
                            Draw.text((X, Y), f'{Byte:02X}', font=Font, fill=Color)
                            X = 8 + int((11 + Columns * 3 + Cell) * Mono)
                            Draw.text((X, Y), chr(Byte) if 32 <= Byte <= 126 else '.', font=Font, fill=Color)
                        Y += Line
                else:
                    Text(8, Y, 'Select a packet from List to inspect its hex.')
                Text(8, Height - Line - 3,
                     f"{len(self.Events)} retained | UI skipped {self.Skipped} | server evicted {self.Evicted} | {self.Status}", Width=PanelValue.Width - 28)
            Draw.line((PanelValue.Width - 15, Height - 3, PanelValue.Width - 3, Height - 15), fill=Orange, width=2)
            Frames.append((Index, PanelValue.X, PanelValue.Y, PanelValue.Width, Height, ImageValue.tobytes()))
        # Software cursor exists only while overlay controls own input. Never
        # retain a game cursor or force global OS cursor visibility/clip state.
        if self.EditMode and getattr(self, 'SoftwareCursor', True):
            CursorImage = Image.new('RGBA', (12, 16), (0, 0, 0, 0))
            ImageDraw.Draw(CursorImage).polygon([(0, 0), (0, 14), (4, 10), (10, 10)], fill=(255, 190, 90, 255), outline=(0, 0, 0, 255))
            Frames.append((2, max(0, min(self.Screen[0] - 12, self.Mouse[0])),
                           max(0, min(self.Screen[1] - 16, self.Mouse[1])), 12, 16, CursorImage.tobytes()))
        else:
            Frames.append((2, 0, 0, 0, 0, b''))
        return Frames
