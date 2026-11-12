"""Native Windows private-game startup; Linux continues using proton_client.py.
Launches a private copy of the selected executable suspended, with one-use private state
received through stdin. Never patches installation files or retail Documents.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from private_friends_feed import PrivateFriendsFeed
import re
import shutil
import signal
import socket
import stat
import sys
import threading
import time
import uuid

from tutorial_history import TutorialHistory
from overlay_frame import OverlayFeed
from proton_client import EXPECTED, PORTS, bridge

HERE = Path(__file__).resolve().parent
EXECUTABLE_SHA256 = '31c74abfedb52fa2ef8342e8f434d766184eca32d85ea9419bcbb46c09a6e379'
INSTALLATION_MANIFESTS = frozenset(('ww.vdf', 'uplay_install.manifest', 'uplay_manifest.xml'))
STAGE_MARKER = '.division-private-stage'
STAGE_IDENTITY = b'Division Private Launcher owned Windows stage v1\n'


def link_asset_directory(source, destination):
    # NTFS directory junctions need neither symlink privilege nor Developer Mode.
    # Only the large asset directories are shared; root files are independent copies.
    import _winapi
    _winapi.CreateJunction(str(source), str(destination))


def stage_game(game, destination):
    game = game.resolve()
    if destination.resolve().is_relative_to(game):
        raise ValueError('Keep the launcher data folder outside the game installation')
    destination.mkdir()
    (destination/STAGE_MARKER).write_bytes(STAGE_IDENTITY)
    for source in game.iterdir():
        if source.name.lower() in INSTALLATION_MANIFESTS or source.name.lower()==STAGE_MARKER:
            continue
        target = destination/source.name
        if source.is_dir():
            link_asset_directory(source.resolve(), target)
        elif source.is_file():
            shutil.copy2(source, target)
        else:
            raise ValueError('Unsupported entry in selected game folder')
    # Recheck the actual launch image, including copies made after preflight.
    with (destination/'thedivision.exe').open('rb') as executable:
        if hashlib.file_digest(executable, 'sha256').hexdigest() != EXECUTABLE_SHA256:
            raise ValueError('Selected executable changed during staging')


def prepare_stage(game, destination):
    # Called only while all exclusive relay listeners are held. A stable game
    # executable path lets Windows reuse its application firewall rule. Recover
    # only an explicitly owned old stage, never an arbitrary existing directory.
    if destination.resolve().is_relative_to(game.resolve()) or game.resolve().is_relative_to(destination.resolve()):
        raise ValueError('Keep the private game stage separate from the selected installation')
    if destination.is_symlink() or destination.is_junction():
        raise ValueError('Private game stage must not be a directory link')
    if destination.exists():
        marker=destination/STAGE_MARKER
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size!=len(STAGE_IDENTITY) or marker.read_bytes()!=STAGE_IDENTITY:
            raise ValueError('Existing game stage is not owned by this launcher')
        remove_stage(destination)
    stage_game(game,destination)


def remove_stage(client):
    # Only copied root files can have their attributes relaxed for removal.
    # Never change attributes through an asset junction or follow it for cleanup.
    for entry in client.iterdir():
        if not entry.is_symlink() and not entry.is_junction() and entry.is_file():
            entry.chmod(stat.S_IREAD | stat.S_IWRITE)
    for attempt in range(4):
        try:
            shutil.rmtree(client)
            return
        except OSError:
            if attempt==3: raise
            time.sleep(.25)


class PlatformRelaunchRequested(RuntimeError):
    pass


def memory_status():
    """Read Windows capacity counters only; no process memory or identity data."""
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_=[('length',ctypes.c_uint32),('load',ctypes.c_uint32)]+[(name,ctypes.c_uint64) for name in
            ('total_physical','available_physical','total_page','available_page','total_virtual','available_virtual','extended_virtual')]
    value=MemoryStatus();value.length=ctypes.sizeof(value)
    query=ctypes.WinDLL('kernel32',use_last_error=True).GlobalMemoryStatusEx
    query.argtypes=[ctypes.POINTER(MemoryStatus)];query.restype=ctypes.c_int
    if not query(ctypes.byref(value)): raise OSError('Memory status unavailable')
    return {name:int(getattr(value,name)//1048576) for name in
            ('total_physical','available_physical','total_page','available_page')}


def fault_metadata(payload):
    # Never preserve arbitrary registers, exception text or account/process memory.
    result = {name: payload[name] for name in ('rva', 'hresult')
              if type(payload.get(name)) is int and 0 <= payload[name] <= 0xffffffff}
    if payload.get('type') in ('access-violation', 'illegal-instruction', 'abort',
                              'arithmetic', 'breakpoint', 'stack-overflow', 'guard-page'):
        result['type'] = payload['type']
    return result


def validate_handoff(value):
    if not isinstance(value, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{24}', value.get('grant', '')):
        raise ValueError('Invalid private launch identity')
    uuid.UUID(value['realmId']); uuid.UUID(value['accountId'])
    if not isinstance(value.get('gamertag'), str) or not value['gamertag'] or any(ord(c)<32 for c in value['gamertag']):
        raise ValueError('Invalid private display name')
    rows = value.get('endpoints', [])
    if len(rows)!=4 or {entry['role'] for entry in rows}!=set(PORTS):
        raise ValueError('Incomplete private endpoints')
    endpoints = {}
    for entry in rows:
        host, port = entry['host'], entry['port']
        if not isinstance(host,str) or not host or any(c.isspace() for c in host) or not isinstance(port,int) or not 1<=port<=65535:
            raise ValueError('Invalid private endpoint')
        # Never relay a loopback endpoint into one of this adapter's listeners.
        if host.lower() in ('localhost','127.0.0.1','::1') and port in PORTS.values():
            raise ValueError('Server endpoint overlaps local adapter port')
        endpoints[entry['role']] = (host,port)
    for name in ('certificatePem','clientCertificatePem','clientPrivateKeyPem'):
        if not isinstance(value.get(name),str) or not value[name].startswith('-----BEGIN ') or len(value[name])>32768:
            raise ValueError('Invalid private TLS material')
    return endpoints


class CountedSocket:
    """Count transported bytes without inspecting or retaining their contents."""
    def __init__(self, stream):
        self.stream, self.received = stream, 0

    def recv(self, count):
        data = self.stream.recv(count)
        self.received += len(data)
        return data

    def sendall(self, data):
        return self.stream.sendall(data)

    def shutdown(self, how):
        return self.stream.shutdown(how)


def bind_relays(endpoints, stopped, diagnostic=lambda *args: None):
    listeners, connections, gate = [], set(), threading.Lock()
    slots = threading.BoundedSemaphore(32)
    def serve(client, endpoint, role):
        upstream = None
        incoming, outgoing = CountedSocket(client), None
        phase, code = 'upstream_connect', 0
        try:
            client.settimeout(120)
            upstream = socket.create_connection(endpoint, timeout=15)
            upstream.settimeout(120)
            if upstream.getpeername()[0] == '127.0.0.1' and upstream.getpeername()[1] in PORTS.values():
                raise OSError('Private endpoint resolves into adapter listener')
            with gate: connections.add(upstream)
            phase = 'stream'
            outgoing = CountedSocket(upstream)
            bridge(incoming, outgoing)
        except OSError as error:
            code = getattr(error, 'winerror', None) or error.errno or 0
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    sock.close()
                    with gate: connections.discard(sock)
            slots.release()
            diagnostic(role, phase, code, incoming.received, outgoing.received if outgoing else 0)
    def accept(listener, endpoint, role):
        while not stopped.is_set():
            try: client, _ = listener.accept()
            except socket.timeout: continue
            except OSError: return
            if not slots.acquire(blocking=False): client.close(); continue
            with gate: connections.add(client)
            threading.Thread(target=serve,args=(client,endpoint,role),daemon=True).start()
    def close():
        stopped.set()
        for sock in listeners: sock.close()
        with gate:
            for sock in connections:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                sock.close()
    try:
        # Bind every role before accepting anything; a conflict fails atomically.
        for role, port in PORTS.items():
            listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            listeners.append(listener)
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            listener.bind(('127.0.0.1',port)); listener.listen(16); listener.settimeout(.5)
        for (role,_), listener in zip(PORTS.items(),listeners):
            threading.Thread(target=accept,args=(listener,endpoints[role],role),daemon=True).start()
        return close
    except BaseException:
        close(); raise


def check(args):
    if sys.platform!='win32' or sys.maxsize<2**32: raise RuntimeError('Native Windows x64 runtime required')
    for name in ('thedivision.exe','uplay_r1_loader64.dll'):
        if not (args.game/name).is_file(): raise RuntimeError('Game folder is missing '+name)
    with (args.game/'thedivision.exe').open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=EXECUTABLE_SHA256:
            raise RuntimeError('Unsupported executable build; installation was not changed')
    import frida
    if frida.__version__!='17.4.0': raise RuntimeError('Bundled Frida 17.4.0 required')
    from PIL import ImageFont
    ImageFont.load_default(size=14)
    for name in ('startup_agent.js','private_friends_agent.js','private_friends_feed.py','windows_environment_agent.js','overlay/agent.js','overlay/bridge.c'):
        if not (HERE/name).is_file(): raise RuntimeError('Incomplete adapter package: '+name)
    # Check local socket ownership without contacting any server.
    sockets=[]
    try:
        for port in PORTS.values():
            sock=socket.socket(); sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.bind(('127.0.0.1',port))
    except OSError as error:
        raise RuntimeError('Private adapter ports are busy; close the previous private game') from error
    finally:
        for sock in sockets: sock.close()


def run(args, handoff):
    import frida
    endpoints=validate_handoff(handoff)
    state=args.state_directory.resolve()
    for path in (args.addon_events,args.overlay_snapshot):
        if path and not path.resolve().is_relative_to(state/'captures'):
            raise ValueError('Addon paths must remain under private launcher captures')
    folder=state/'runtime'/uuid.uuid4().hex[:16]
    client=state/'private-game'
    documents=folder/'documents'
    documents.mkdir(parents=True)
    if len(str(documents))>220 or '"' in str(documents):
        raise ValueError('Extract the launcher into a shorter local path')
    for name, field in [('server.pem','certificatePem'),('client.pem','clientCertificatePem'),('client.key','clientPrivateKeyPem')]:
        (folder/name).write_text(handoff[field],encoding='ascii')
    stopped=threading.Event()
    cleanup=None; pid=None; session=None; history=None
    stage_owned=False
    terminated=threading.Event()
    platform_restart=threading.Event()
    device=None
    fault_count=0
    phase='preparing'
    log=(folder/'client-adapter.log').open('w')
    diagnostic_lock=threading.Lock()
    relay_reports=0
    network_reports=0
    addon=args.addon_events.open('a') if args.addon_events else None
    def relay_diagnostic(role, phase, code, sent, received):
        nonlocal relay_reports
        with diagnostic_lock:
            if relay_reports>=64 or log.closed: return
            relay_reports+=1
            log.write(f'{time.time():.3f} relay_closed role={role} phase={phase} code={code} client_bytes={sent} server_bytes={received}\n')
            log.flush()
    def record(event):
        if isinstance(event,str) and re.fullmatch('[a-z_]+',event):
            if event=='steam_restart_requested': platform_restart.set()
            line=f'{time.time():.3f} {event}\n';log.write(line);log.flush()
            if addon: addon.write(line);addon.flush()
    def save(final=False):
        if not history: return
        try:
            if history.save(final=final):record('tutorial_history_saved')
        except (OSError,ValueError): record('tutorial_history_save_failed')
    def agent_message(message, _):
        nonlocal fault_count, network_reports
        payload=message.get('payload',{})
        if not isinstance(payload,dict):record('agent_error');return
        record(payload.get('event','agent_error'))
        if payload.get('event')=='private_network_result' and network_reports<16:
            operation, code, result = payload.get('operation'), payload.get('error'), payload.get('result')
            if operation in ('connect','wsa_connect','connect_ex','send') and type(code) is int and 0<=code<=0xffffffff and type(result) is int and -1<=result<=0x7fffffff:
                network_reports+=1
                log.write(f'{time.time():.3f} network_details operation={operation} result={result} error={code} blocked={payload.get("blocked") is True}\n')
                family, port = payload.get('family'), payload.get('port')
                if type(family) is int and 0<=family<=65535 and type(port) is int and 0<=port<=65535:
                    log.write(f'{time.time():.3f} network_destination family={family} port={port} loopback={payload.get("loopback") is True}\n')
                log.flush()
        if payload.get('event')=='client_fault' and fault_count<4:
            fault_count+=1
            fault=fault_metadata(payload)
            with (folder/'client-faults.jsonl').open('a') as output:
                output.write(json.dumps(fault)+'\n')
            # Include the safe numeric detail in the screenshot-friendly log too.
            detail=' '.join(name+'='+f'0x{fault[name]:08x}' for name in ('rva','hresult') if name in fault)
            if 'type' in fault:detail+=' type='+fault['type']
            log.write(f'{time.time():.3f} client_fault_details {detail}\n');log.flush()
    print('PRIVATE CLIENT:',folder,flush=True)
    def stop(*_): stopped.set()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        # Own the process-wide relay ports before touching the shared stable
        # stage; a second launch cannot clean up the first game's executable.
        cleanup=bind_relays(endpoints,stopped,relay_diagnostic);record('loopback_relays_ready')
        record('game_staging_started')
        phase='staging'
        prepare_stage(args.game,client)
        stage_owned=True
        record('private_game_directory_ready')
        device=frida.get_local_device()
        history=TutorialHistory(state/'profiles/client-state',folder,handoff['realmId'],handoff['accountId'],handoff['grant'],
            documents=documents/'My Games'/'Tom Clancy\'s The Division')
        try:record('tutorial_history_restored' if history.restore() else 'tutorial_history_new_account')
        except (OSError,ValueError):record('tutorial_history_restore_failed')
        from display_settings import apply_display
        apply_display(folder, history.documents, getattr(args, 'display_mode', 'default'),
                      getattr(args, 'display_width', 1280), getattr(args, 'display_height', 720))
        record('game_spawn_requested')
        phase='spawning'
        pid=device.spawn([str(client/'thedivision.exe')],cwd=str(client))
        session=device.attach(pid);record('game_attached')
        def detached(reason,crash):
            if reason=='process-terminated':terminated.set()
            if reason in ('application-requested','process-replaced','process-terminated','connection-terminated','device-lost'):
                record('game_detached_'+reason.replace('-','_'))
            record('game_crashed' if crash else 'game_detached');stopped.set()
        session.on('detached',detached)
        defaults=('-sdfonly -demo -proxylist 127.0.0.1 -proxylist_port 25100 -online -noserver '
            f'-startgame -login_unsecure -bakedlevel -username {handoff["grant"]} '
            f'-ssl_remote_cert "{folder/"server.pem"}" -ssl_local_cert "{folder/"client.pem"}" '
            f'-ssl_local_key "{folder/"client.key"}"')
        source='globalThis.PRIVATE_WINDOWS_CONFIG='+json.dumps({'documents':str(documents)})+';\n'
        source+=(HERE/'windows_environment_agent.js').read_text()+'\n'
        source+='globalThis.PRIVATE_STARTUP_CONFIG='+json.dumps({'defaults':defaults,'expectedDefaults':EXPECTED,
            'displayName':handoff['gamertag'],'aimActor':'','diagnostics':True,'privateFriends':bool(args.friends_snapshot)})+';\n'
        source+=(HERE/'private_friends_agent.js').read_text()+'\n'
        source+=(HERE/'startup_agent.js').read_text()
        feed=OverlayFeed(args.overlay_snapshot) if args.overlay_snapshot else None
        if feed:
            source+='\nglobalThis.PRIVATE_OVERLAY_SOURCE='+json.dumps((HERE/'overlay/bridge.c').read_text())+';\n'
            source+=(HERE/'overlay/agent.js').read_text()
            source+='\nrpc.exports.overlaystatus=()=>PrivateOverlay.status();\nrpc.exports.overlayinput=()=>PrivateOverlay.input();\n'
        script=session.create_script(source)
        script.on('message',agent_message)
        script.load();record('startup_agent_loaded')
        phase='initial_status'
        status=script.exports_sync.status()
        if status['failed'] or not status['armed']: raise RuntimeError('Private startup rejected client build')
        handoff.clear();device.resume(pid);record('game_resumed')
        phase='startup_status'
        friends_feed=PrivateFriendsFeed(args.friends_snapshot)
        startup_complete=False
        next_history=next_status=next_overlay=0
        next_memory=0;memory_reports=0
        deadline=time.monotonic()+45
        while not stopped.wait(.05 if feed else .5) and not (folder/'stop.request').exists():
            now=time.monotonic()
            try: friends_feed.poll(script)
            except Exception: record('private_friends_feed_failed')
            if memory_reports<16 and now>=next_memory:
                memory_reports+=1;next_memory=now+30
                try:
                    counters=memory_status()
                    log.write(f'{time.time():.3f} memory_mb '+ ' '.join(f'{name}={value}' for name,value in counters.items())+'\n');log.flush()
                except (OSError,AttributeError): record('memory_status_unavailable')
            if now>=next_history:save();next_history=now+1
            if not startup_complete and now>=next_status:
                try:
                    status=script.exports_sync.status();next_status=now+.5
                except frida.InvalidOperationError:
                    # Detach notification can lag the destroyed-script response.
                    if stopped.wait(.5):break
                    raise
                if status['failed'] or not status['done'] and now>deadline:
                    raise RuntimeError('Private startup did not complete')
                if status['done']:
                    startup_complete=True;phase='running';record('startup_status_complete')
            if feed:
                try:
                    for index,x,y,width,height,frame in feed.Next(script.exports_sync.overlayinput()):
                        script.post({'type':'overlay-frame','index':index,'x':x,'y':y,'width':width,'height':height},frame)
                    if now>=next_overlay:
                        (folder/'overlay-status.json').write_text(json.dumps(script.exports_sync.overlaystatus()))
                        next_overlay=now+5
                except Exception as error:
                    if stopped.is_set(): record('overlay_feed_stopped_with_game')
                    else:
                        record('overlay_feed_failed')
                        kind=re.sub('[^a-z_]','',type(error).__name__.lower())
                        record('overlay_error_'+kind)
                    feed=None
        if platform_restart.is_set(): raise PlatformRelaunchRequested()
    except PlatformRelaunchRequested:
        record('private_game_ended_for_steam_restart')
        raise
    except Exception as error:
        record('windows_adapter_failed')
        # Error class only: native exceptions may include credentials/command text.
        (folder/'runtime.log').write_text('Native adapter failure: '+type(error).__name__+
            '\nPhase: '+phase+'\nPrior client fault events: '+str(fault_count)+'\n')
        raise RuntimeError('Native startup failed; inspect the private run folder') from None
    finally:
        if pid is not None and not terminated.is_set():
            try:device.kill(pid)
            except (frida.ProcessNotFoundError,frida.InvalidOperationError,frida.TransportError):pass
        save(final=True)
        if session:
            try:session.detach()
            except (frida.ProcessNotFoundError,frida.InvalidOperationError,frida.TransportError):pass
        (folder/'client.key').unlink(missing_ok=True)
        if stage_owned and client.exists():
            try:
                # Python >=3.8 removes Windows junctions without traversing targets.
                remove_stage(client)
                record('private_game_directory_removed')
            except OSError:
                record('private_game_directory_cleanup_deferred')
        if cleanup:cleanup()
        with diagnostic_lock: log.close()
        if addon:addon.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,required=True)
    parser.add_argument('--state-directory',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--addon-events',type=Path)
    parser.add_argument('--friends-snapshot',type=Path)
    from display_settings import add_arguments as add_display_arguments
    add_display_arguments(parser)
    parser.add_argument('--overlay-snapshot',type=Path)
    args=parser.parse_args();args.game=args.game.resolve()
    try:
        check(args)
        if args.check:print('Windows game installation and bundled adapter verified.');return 0
        raw=sys.stdin.buffer.readline(131073)
        if len(raw)>131072 or not raw.endswith(b'\n'):raise ValueError('Invalid handoff size')
        run(args,json.loads(raw));return 0
    except PlatformRelaunchRequested:
        print('The installed game requested a Steam relaunch. See client-adapter.log.',file=sys.stderr)
        return 20
    except Exception as error:
        if args.check: print(str(error),file=sys.stderr)
        else: print('Private Windows launch failed; inspect the private run log.',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
