"""Focused contracts: filtering, retained evidence, redaction, bounded layout/input."""
from pathlib import Path
import base64
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from overlay_ui import OverlayUi, PacketIds, MAX_EVENTS
from PIL import Image

class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.Temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.Ui = OverlayUi(Path(self.Temporary.name)/'layout.json', {'game_server/S2C/18':{'role':'Entity state'}})

    def tearDown(self):
        self.Temporary.cleanup()

    def Feed(self, Index=1, Redacted=False):
        self.Ui.Ingest({'version':2, 'poseEnabled':True, 'packetsEnabled':True,
            'pose':'POS 515.25 -0.13 602.31 | DIR5C 0.31 0.00 -0.95 | 0.2s', 'status':'Private observations',
            'events':[{'index':Index,'event':{'kind':'packet','service':'game_server','direction':'S2C','messageId':18,
                'idBytes':1,'payloadBytes':255,'message':base64.b64encode(bytes(range(256))).decode(),
                'redacted':Redacted,'time':'2026-10-29T12:34:56Z'}}]})

    def test_filter_and_unknowns(self):
        self.assertIsNone(PacketIds('ALL'))
        self.assertEqual(PacketIds('18, 0x1f; 438'),{18,31,438})
        for Value in ('nope','18-438','4294967296'):
            with self.assertRaises(ValueError): PacketIds(Value)
        self.Feed(); self.assertEqual(len(self.Ui.Matching()),1)
        self.Ui.Ids={31}; self.assertFalse(self.Ui.Matching())
        self.assertEqual(self.Ui.Role({'service':'auth','direction':'S2C','messageId':18}),'Unknown role')

    def test_redaction_and_eviction_pinned_selection(self):
        self.Feed(); self.Ui.Selected=self.Ui.Events[1]
        for Index in range(2,MAX_EVENTS+10): self.Feed(Index)
        self.assertEqual(len(self.Ui.Events),MAX_EVENTS)
        self.assertEqual(self.Ui.Selected['Raw'],bytes(range(256)))
        self.Feed(1000,True); self.assertEqual(self.Ui.Events[1000]['Raw'],b'')

    def test_drag_resize_scaling_and_preferences(self):
        self.Feed(); self.Ui.Render()
        self.Ui.Handle(1,20,20,0,1); self.Ui.Handle(0,99999,99999,0,1); self.Ui.Handle(2,99999,99999,0,1)
        Panel=self.Ui.Panels[0]
        self.assertLessEqual(Panel.X+Panel.Width,1920)
        self.Ui.Handle(3,Panel.X+2,Panel.Y+2,120,2)
        self.Ui.Handle(3,Panel.X+2,Panel.Y+2,-120,4)
        Restored=OverlayUi(self.Ui.Settings)
        self.assertEqual(Restored.Panels[0].Font,Panel.Font)
        self.assertEqual(Restored.Panels[0].Opacity,Panel.Opacity)
        self.Ui.Input({'screen':[640,360],'edit':True,'mouse':[100,100]})
        for _,X,Y,Width,Height,Pixels in self.Ui.Render():
            self.assertEqual(len(Pixels),Width*Height*4)
            self.assertLessEqual(X+Width,640)
            self.assertLessEqual(Y+Height,360)

    def test_window_controls_restore_and_persist_without_stopping_capture(self):
        self.Feed()
        def Control(Command, Index):
            self.Ui.Render()
            Rect=next(Rect for Rect,Action in self.Ui.Hits if Action==(Command,Index))
            X,Y=Rect[0]+2,Rect[1]+2
            self.Ui.Handle(1,X,Y,0,0)
            self.assertIsNone(self.Ui.Drag)  # Buttons never initiate title dragging.
            self.Ui.Handle(2,X,Y,0,0)
        self.Ui.Input({'edit':True,'softwareCursor':False})
        Control('header',0); self.assertTrue(self.Ui.Panels[0].Header)
        Control('minimize',1)
        Height,_,Collapsed=self.Ui.PanelFrame(1)
        self.assertTrue(Collapsed); self.assertLess(Height,60)
        Panel=self.Ui.Panels[1]
        self.assertIsNone(self.Ui.PanelAt(Panel.X+30,Panel.Y+100))
        Control('minimize',1); self.assertFalse(self.Ui.Panels[1].Minimized)
        Control('close',1)
        self.Ui.Input({'edit':False,'softwareCursor':False})
        self.assertEqual(next(Frame for Frame in self.Ui.Render() if Frame[0]==1)[3:5],(0,0))
        self.Feed(2); self.assertEqual(len(self.Ui.Events),2)  # Closing is presentation only.
        Restored=OverlayUi(self.Ui.Settings)
        self.assertTrue(Restored.Panels[1].Closed); self.assertTrue(Restored.Panels[0].Header)
        self.Ui.Input({'edit':True,'softwareCursor':False})
        Control('show',1)
        self.assertFalse(self.Ui.Panels[1].Closed)
        Control('header',0)
        self.Ui.Input({'edit':False,'softwareCursor':False})
        self.assertEqual(self.Ui.PanelFrame(0)[1],0)

    def test_scroll_freezes_history_and_follow_returns_live(self):
        for Index in range(1,50): self.Feed(Index)
        self.Ui.Render()
        Panel=self.Ui.Panels[1]
        self.Ui.Handle(3,Panel.X+30,Panel.Y+180,-120,0)
        self.assertTrue(self.Ui.Paused)
        Previous=self.Ui.Frozen[0]['Index']
        self.Feed(50); self.assertEqual(self.Ui.Frozen[0]['Index'],Previous)
        self.Ui.Render()
        Rect=next(Rect for Rect,Action in self.Ui.Hits if Action=='follow')
        self.Ui.Click(Rect[0]+1,Rect[1]+1)
        self.assertFalse(self.Ui.Paused); self.assertEqual(self.Ui.ListScroll,0)
        self.assertEqual(self.Ui.Matching()[0]['Index'],50)

    def test_cursor_visibility_does_not_duplicate_native_pointer(self):
        self.Feed()
        self.Ui.Input({'edit':True,'softwareCursor':False,'mouse':[320,180]})
        self.assertEqual(next(Frame for Frame in self.Ui.Render() if Frame[0]==2)[3:5],(0,0))
        self.Ui.Input({'edit':True,'softwareCursor':True,'mouse':[320,180]})
        self.assertEqual(next(Frame for Frame in self.Ui.Render() if Frame[0]==2)[1:5],(320,180,12,16))
        self.Ui.Input({'edit':False,'softwareCursor':False})
        self.assertEqual(next(Frame for Frame in self.Ui.Render() if Frame[0]==2)[3:5],(0,0))

    def test_select_row_opens_full_hex_and_scroll_is_bounded(self):
        self.Feed(); self.Ui.Render()
        Rect,Event=next((Rect,Action) for Rect,Action in self.Ui.Hits if isinstance(Action,dict))
        self.Ui.Click(Rect[0]+1,Rect[1]+1)
        self.assertEqual(self.Ui.Tab,'HEX'); self.assertEqual(self.Ui.Selected['Raw'],bytes(range(256)))
        self.Ui.HexScroll=999999; self.Ui.Dirty=True
        Frames=self.Ui.Render()
        self.assertLess(self.Ui.HexScroll,256)
        for Index,X,Y,Width,Height,Pixels in Frames:
            if Index==1:
                self.assertGreater(Pixels[3],0); self.assertLess(Pixels[3],255)

if __name__=='__main__': unittest.main()
