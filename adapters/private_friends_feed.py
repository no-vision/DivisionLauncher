"""Bounded, credential-free private roster feed shared by native and Proton adapters."""
import json, time
from pathlib import Path

class PrivateFriendsFeed:
    def __init__(self, path):
        self.path=Path(path) if path else None
        self.next=0
        self.last=None
    def poll(self, script):
        if not self.path or time.monotonic()<self.next:return
        self.next=time.monotonic()+1
        try:
            with self.path.open('rb') as source:raw=source.read(131073)
            if len(raw)>131072:raise ValueError('private roster size')
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('expires'),(float,int)) or not isinstance(data.get('entries'),list):raise ValueError('private roster format')
            entries=data['entries'] if time.time()<data['expires'] else []
        except (OSError,ValueError,TypeError):entries=[]
        if entries!=self.last:
            script.exports_sync.privatefriends(entries)
            self.last=entries
