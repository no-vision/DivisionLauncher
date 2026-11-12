"""Critical native adapter contracts without launching a retail process."""
import argparse
import io
import json
import copy
import hashlib
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import windows_client as Client


def Handoff():
    return dict(grant='a'*24,realmId='11111111-1111-1111-1111-111111111111',
        accountId='22222222-2222-2222-2222-222222222222',gamertag='Private tester',
        certificatePem='-----BEGIN CERTIFICATE-----\nfixture',clientCertificatePem='-----BEGIN CERTIFICATE-----\nfixture',
        clientPrivateKeyPem='-----BEGIN EC PRIVATE KEY-----\nfixture',
        endpoints=[dict(role=role,host='server.example.test',port=44100+i) for i,role in enumerate(Client.PORTS)])


class WindowsAdapterChecks(unittest.TestCase):
    def test_transport_counter_does_not_change_bytes(self):
        stream=types.SimpleNamespace(recv=lambda count:b'\x16\x03\x03',sendall=lambda data:data,shutdown=lambda how:how)
        counted=Client.CountedSocket(stream)
        self.assertEqual(counted.recv(3),b'\x16\x03\x03')
        self.assertEqual(counted.received,3)
        self.assertEqual(counted.sendall(b'unchanged'),b'unchanged')
        self.assertFalse(hasattr(counted,'data'))
    def test_fault_metadata_preserves_codes_without_private_memory(self):
        self.assertEqual(Client.fault_metadata(dict(rva=0x36c490,hresult=0x887a0005,
            type='access-violation',registers='private data',message='private data')),
            dict(rva=0x36c490,hresult=0x887a0005,type='access-violation'))
        self.assertEqual(Client.fault_metadata(dict(rva=-1,hresult=True,type='untrusted text')), {})
    def test_staging_excludes_manifests_preserves_assets_and_installation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp).resolve();game=root/'Game';game.mkdir()
            (game/'thedivision.exe').write_bytes(b'executable fixture')
            (game/'uplay_r1_loader64.dll').write_bytes(b'loader fixture')
            (game/'WW.VDF').write_bytes(b'original manifest')
            for name in ('uplay_install.manifest','uplay_manifest.xml'):
                (game/name).write_bytes(b'original manifest')
            (game/'rogue').mkdir();(game/'rogue'/'asset.sdf').write_bytes(b'asset fixture')
            destination=root/'client'
            digest=hashlib.sha256(b'executable fixture').hexdigest()
            with patch.object(Client,'EXECUTABLE_SHA256',digest),patch.object(Client,'link_asset_directory',lambda src,dst:dst.symlink_to(src,target_is_directory=True)):
                Client.prepare_stage(game,destination)
                self.assertFalse(any(p.name.lower() in Client.INSTALLATION_MANIFESTS for p in destination.iterdir()))
                self.assertEqual((destination/'rogue'/'asset.sdf').read_bytes(),b'asset fixture')
                (destination/'thedivision.exe').write_bytes(b'private change')
                self.assertEqual((game/'thedivision.exe').read_bytes(),b'executable fixture')
                Client.prepare_stage(game,destination)
                self.assertEqual((destination/'thedivision.exe').read_bytes(),b'executable fixture')
                with self.assertRaises(ValueError):Client.prepare_stage(destination,destination)
                self.assertTrue((destination/'thedivision.exe').exists())
                Client.remove_stage(destination)
                self.assertEqual((game/'rogue'/'asset.sdf').read_bytes(),b'asset fixture')
                self.assertEqual((game/'WW.VDF').read_bytes(),b'original manifest')
                with self.assertRaises(ValueError):Client.stage_game(game,game/'nested')
                self.assertFalse((game/'nested').exists())
                destination.mkdir();(destination/'user-file').write_text('preserve')
                with self.assertRaises(ValueError):Client.prepare_stage(game,destination)
                self.assertEqual((destination/'user-file').read_text(),'preserve')

    def test_handoff_roles_and_no_loopback_recursion(self):
        self.assertEqual(set(Client.validate_handoff(Handoff())),set(Client.PORTS))
        for change in ('grant','duplicate','loop'):
            value=Handoff()
            if change=='grant':value['grant']='bad" command'
            elif change=='duplicate':value['endpoints'][1]=value['endpoints'][0]
            else:value['endpoints'][0].update(host='127.0.0.1',port=25100)
            with self.assertRaises(ValueError):Client.validate_handoff(value)

    def test_busy_relay_never_removes_another_games_stage(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            state=Path(temp).resolve();stage=state/'private-game';stage.mkdir()
            (stage/Client.STAGE_MARKER).write_bytes(Client.STAGE_IDENTITY)
            (stage/'thedivision.exe').write_bytes(b'active game fixture')
            args=argparse.Namespace(game=state/'installation',state_directory=state,addon_events=None,overlay_snapshot=None,friends_snapshot=None)
            with patch.dict('sys.modules',{'frida':types.SimpleNamespace()}),patch.object(Client,'bind_relays',side_effect=OSError('busy')),patch.object(Client.signal,'signal'):
                with self.assertRaises(RuntimeError):Client.run(args,Handoff())
            self.assertEqual((stage/'thedivision.exe').read_bytes(),b'active game fixture')

    def test_steam_relaunch_has_distinct_exit_code(self):
        stdin=types.SimpleNamespace(buffer=io.BytesIO((json.dumps(Handoff())+'\n').encode()))
        with patch.object(Client.sys,'argv',['windows_client.py','--game','.','--state-directory','.']),patch.object(Client.sys,'stdin',stdin),patch.object(Client,'check'),patch.object(Client,'run',side_effect=Client.PlatformRelaunchRequested),patch.object(Client.sys,'stderr',io.StringIO()):
            self.assertEqual(Client.main(),20)

    def test_selected_folder_suspended_start_private_history_and_cleanup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp).resolve();game=root/'Selected Game';game.mkdir()
            (game/'thedivision.exe').write_bytes(b'executable fixture')
            state=root/'launcher'; state.mkdir()
            args=argparse.Namespace(game=game,state_directory=state,addon_events=None,overlay_snapshot=None,friends_snapshot=None)
            calls=[];source=[];callbacks={};stops=[];messages={}
            script=types.SimpleNamespace(exports_sync=types.SimpleNamespace(status=lambda:dict(armed=1,failed=False,done=False)),
                on=lambda name,fn:messages.update({name:fn}),load=lambda:calls.append('load'))
            session=types.SimpleNamespace(on=lambda name,fn:callbacks.update({name:fn}),
                create_script=lambda text:(source.append(text) or script),detach=lambda:calls.append('detach'))
            def spawn(argv,cwd):
                staged=state/'private-game'
                self.assertEqual(argv,[str(staged/'thedivision.exe')]);self.assertEqual(cwd,str(staged))
                self.assertEqual((staged/'thedivision.exe').read_bytes(),b'executable fixture')
                calls.append('spawn');return 1234
            def resume(pid):
                self.assertEqual(calls[:3],['spawn','attach','load'])
                for _ in range(6):
                    messages['message']({'payload':dict(event='client_fault',rva=0x36c490,
                        hresult=0x887a0005,type='access-violation',secret='not for logging')},None)
                calls.append('resume');callbacks['detached']('process-terminated',None)
            device=types.SimpleNamespace(spawn=spawn,attach=lambda pid:(calls.append('attach') or session),
                resume=resume,kill=lambda pid:calls.append('kill'))
            module=types.SimpleNamespace(get_local_device=lambda:device,ProcessNotFoundError=RuntimeError,
                InvalidOperationError=RuntimeError,TransportError=RuntimeError)
            with patch.dict('sys.modules',{'frida':module}),patch.object(Client,'EXECUTABLE_SHA256',hashlib.sha256(b'executable fixture').hexdigest()),patch.object(Client,'bind_relays',lambda *a:lambda:stops.append(True)),patch.object(Client.signal,'signal'):
                Client.run(args,Handoff())
            self.assertNotIn('kill',calls)  # Never kill a PID after a confirmed process termination.
            self.assertTrue(stops); self.assertIn('PRIVATE_WINDOWS_CONFIG',source[0])
            self.assertIn('PRIVATE_STARTUP_CONFIG',source[0])
            run=next((state/'runtime').iterdir())
            self.assertFalse((run/'client.key').exists())
            self.assertFalse((state/'private-game').exists())
            self.assertTrue((run/'documents').exists())
            self.assertNotIn('a'*24,(run/'client-adapter.log').read_text())
            self.assertIn('hresult=0x887a0005',(run/'client-adapter.log').read_text())
            self.assertEqual(len((run/'client-faults.jsonl').read_text().splitlines()),4)
            self.assertNotIn('not for logging',(run/'client-faults.jsonl').read_text())


if __name__=='__main__':unittest.main()
