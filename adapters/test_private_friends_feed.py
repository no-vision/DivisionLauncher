import json, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from private_friends_feed import PrivateFriendsFeed

class PrivateFriendsFeedTests(unittest.TestCase):
    def test_expiry_and_truncation_clear_instead_of_reusing_identity(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path=Path(folder)/'friends.json'; calls=[]
            script=SimpleNamespace(exports_sync=SimpleNamespace(privatefriends=lambda entries:calls.append(entries)))
            feed=PrivateFriendsFeed(path)
            def write(data):
                path.write_text(json.dumps(data)); feed.next=0; feed.poll(script)
            entry={'accountId':'12345678-1234-1234-1234-123456789abc','name':'Private','online':True}
            write({'expires':time.time()+10,'entries':[entry]})
            write({'expires':time.time()+10,'entries':[entry]})
            self.assertEqual(calls,[[entry]])
            write({'expires':time.time()-1,'entries':[entry]})
            self.assertEqual(calls[-1],[])
            write({'expires':time.time()+10,'entries':[entry]})
            path.write_text('x'*131073); feed.next=0; feed.poll(script)
            self.assertEqual(calls[-1],[])

if __name__=='__main__':unittest.main()
