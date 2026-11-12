#!/usr/bin/env python3
"""Private client platform adapter. Account/gameplay services are entirely C#.

The game runs in its own network/PID namespace and Proton prefix. Loopback
listeners relay encrypted bytes to the selected community server through a
private host Unix socket. No official endpoint is reachable inside the namespace.
The one-use handoff is received on stdin, never in argv, environment or saved files.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
from private_friends_feed import PrivateFriendsFeed
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from tutorial_history import TutorialHistory
from display_settings import add_arguments as add_display_arguments, apply_display
from overlay_frame import OverlayFeed

HERE = Path(__file__).resolve().parent
STATE = HERE
PORTS = {'proxy': 25100, 'lobby': 25500, 'world': 25501, 'ping': 25502}
ROLES = tuple(PORTS)
EXPECTED = ('-sdfonly -demo -proxylist tctd-pc-echo.ubisoft.com -proxylist_port 51000 '
    '-online -noserver -startgame -login_ubiservices -bakedlevel -uplay_launcher '
    '-use_public_key_store -public_key_store_address tctd-pc.ubisoft.com '
    '-public_key_store_port 27015 -retail_certs')


def win(path):
    return 'Z:' + str(path).replace('/', '\\')


def bridge(left, right):
    def pump(source, target):
        try:
            while data := source.recv(65536):
                target.sendall(data)
        except OSError:
            pass
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    thread = threading.Thread(target=pump, args=(left, right), daemon=True)
    thread.start()
    pump(right, left)
    thread.join(timeout=2)


def listen(listener, connect, stopped):
    def handle(client):
        with client:
            try:
                with connect(client) as upstream:
                    client.settimeout(120)
                    upstream.settimeout(120)
                    bridge(client, upstream)
            except OSError:
                pass
    listener.listen(32)
    listener.settimeout(.5)
    while not stopped.is_set():
        try:
            client, _ = listener.accept()
        except TimeoutError:
            continue
        threading.Thread(target=handle, args=(client,), daemon=True).start()


def worker(args, handoff):
    run, runtime = args.worker, args.runtime
    links = json.loads(subprocess.check_output(['ip', '-json', 'link', 'show']))
    if {item['ifname'] for item in links} != {'lo'}:
        raise RuntimeError('Client network namespace is not isolated')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    stopped = threading.Event()
    listeners = []
    for role, port in PORTS.items():
        listener = socket.socket()
        listener.bind(('127.0.0.1', port))
        listeners.append(listener)
        def connect(_, role=role):
            upstream = socket.socket(socket.AF_UNIX)
            try:
                upstream.connect(str(run / 'relay.sock'))
                upstream.sendall(bytes([ROLES.index(role)]))
                return upstream
            except BaseException:
                upstream.close()
                raise
        threading.Thread(target=listen, args=(listener, connect, stopped), daemon=True).start()
    for key in list(os.environ):
        if key.startswith(('STEAM_', 'Steam', 'PROTON_', 'WINE')):
            del os.environ[key]
    # dotnet.sh uses a private CLI runtime directory. Proton must retain the
    # desktop session's audio/display socket directory instead of inheriting it.
    desktop_runtime = os.environ.pop('DIVISION_DESKTOP_RUNTIME_DIR', '')
    if desktop_runtime:
        os.environ['XDG_RUNTIME_DIR'] = desktop_runtime
    else:
        os.environ.pop('XDG_RUNTIME_DIR', None)
    for directory in ('compat', 'empty-steam', 'config', 'cache', 'data', 'logs', 'documents', 'desktop'):
        (run / directory).mkdir(mode=0o700, exist_ok=True)
    os.environ.update(STEAM_COMPAT_DATA_PATH=str(run/'compat'), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(run/'empty-steam'),
        STEAM_COMPAT_INSTALL_PATH=str(run/'client'), XDG_CONFIG_HOME=str(run/'config'), XDG_CACHE_HOME=str(run/'cache'),
        XDG_DATA_HOME=str(run/'data'), PROTON_LOG_DIR=str(run/'logs'), WINEDEBUG='warn+all,err+all',
        WINEDLLOVERRIDES='winemenubuilder.exe=d', PROTON_CRASH_REPORT_DIR=str(run/'logs'))
    diagnostics = (os.environ.get('DIVISION_CLIENT_DIAGNOSTICS') == '1' or
                   (STATE/'profiles/client-diagnostics.enabled').exists())
    if diagnostics:
        os.environ.update(DXVK_LOG_PATH=str(run/'logs'), DXVK_LOG_LEVEL='info')
    loader = importlib.machinery.SourceFileLoader('private_copied_proton', str(runtime/'proton'))
    proton = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.path.insert(0, str(runtime))
    loader.exec_module(proton)
    proton.g_proton = proton.Proton(str(runtime))
    proton.g_compatdata = proton.CompatData(str(run/'compat'))
    proton.g_session = proton.Session()
    proton.g_session.init_wine()
    proton.g_session.init_session(True)
    env = proton.g_session.env
    sys.path.insert(0, str(args.frida/'deps-17.4.0'))
    import frida
    if frida.__version__ != '17.4.0':
        raise RuntimeError('This adapter requires Frida 17.4.0')
    manager, device, session, process = frida.get_device_manager(), None, None, None
    token = secrets.token_hex(24)
    log = (run/'client-adapter.log').open('w')
    addon_log = args.addon_events.open('a') if args.addon_events else None
    def record(event):
        # Only fixed event names: no untrusted game memory, credentials or command lines.
        if re.fullmatch('[a-z_]+', event):
            log.write(f'{time.time():.3f} {event}\n')
            log.flush()
            if addon_log:
                addon_log.write(f'{time.time():.3f} {event}\n')
                addon_log.flush()
    history = TutorialHistory(STATE/'profiles/client-state', run,
        handoff['realmId'], handoff['accountId'], handoff['grant'])
    apply_display(run, history.documents, args.display_mode, args.display_width, args.display_height)
    def save_history(final=False):
        try:
            if history.save(final=final):
                record('tutorial_history_saved')
        except (OSError, ValueError):
            # Preferences must not disconnect a playing character. Keep the
            # last good file and a credential-free diagnostic for the launcher.
            record('tutorial_history_save_failed')
    try:
        try:
            record('tutorial_history_restored' if history.restore() else 'tutorial_history_new_account')
        except (OSError, ValueError):
            record('tutorial_history_restore_failed')
        with (run/'frida-server.log').open('wb') as output:
            process = subprocess.Popen([proton.g_proton.wine64_bin, str(args.frida/'frida-server-17.4.0.exe'),
                '--listen', '127.0.0.1:27042', '--token', token], env=env, stdout=output, stderr=output)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                device = manager.add_remote_device('127.0.0.1:27042', token=token)
                device.enumerate_processes()
                break
            except Exception:
                if device:
                    manager.remove_remote_device('127.0.0.1:27042')
                    device = None
                time.sleep(.25)
        if device is None:
            raise RuntimeError('Private client instrumentation did not start')
        pid = device.spawn([win(run/'client/thedivision.exe')], cwd=win(run/'client'))
        session = device.attach(pid)
        detached = threading.Event()
        def on_detached(reason, crash):
            record('game_crashed' if crash else 'game_detached')
            # Frida's enumerated detach reason contains no process memory or credentials.
            if reason in ('application-requested','process-replaced','process-terminated','connection-terminated','device-lost'):
                record('detach_' + reason.replace('-', '_'))
            detached.set()
        session.on('detached', on_detached)
        defaults = ('-sdfonly -demo -proxylist 127.0.0.1 -proxylist_port 25100 -online -noserver '
            f'-startgame -login_unsecure -bakedlevel -username {handoff["grant"]} '
            f'-ssl_remote_cert "{win(run/"server.pem")}" -ssl_local_cert "{win(run/"client.pem")}" '
            f'-ssl_local_key "{win(run/"client.key")}"')
        aim_actor = os.environ.get('DIVISION_AIM_TRACE_ACTOR', '')
        if aim_actor and not re.fullmatch('[a-f0-9]{32}', aim_actor):
            raise RuntimeError('Aim trace requires one private actor GUID')
        source = 'globalThis.PRIVATE_STARTUP_CONFIG=' + json.dumps(dict(defaults=defaults,
            expectedDefaults=EXPECTED, displayName=handoff['gamertag'], aimActor=aim_actor,
            diagnostics=diagnostics, privateFriends=bool(args.friends_snapshot))) + ';\n'
        if aim_actor:
            source += (HERE/'aim_diagnostic.js').read_text() + '\n'
        source += (HERE/'private_friends_agent.js').read_text() + '\n'
        source += (HERE/'startup_agent.js').read_text()
        overlay_feed = None
        if args.overlay_snapshot:
            try:
                overlay_feed = OverlayFeed(args.overlay_snapshot)
            except (ImportError, OSError, ValueError):
                record('overlay_dependency_unavailable')
        if overlay_feed:
            source += '\nglobalThis.PRIVATE_OVERLAY_SOURCE=' + json.dumps((HERE/'overlay/bridge.c').read_text()) + ';\n'
            source += (HERE/'overlay/agent.js').read_text()
            source += '\nrpc.exports.overlaystatus = () => PrivateOverlay.status();\n'
            source += '\nrpc.exports.overlayinput = () => PrivateOverlay.input();\n'
        script = session.create_script(source)
        def agent_message(message, _):
            payload = message.get('payload', {})
            record(payload.get('event', 'agent_error'))
            if aim_actor and payload.get('event') in ('aim_trace', 'aim_trace_armed', 'aim_trace_stopped', 'aim_trace_unavailable'):
                with (run/'aim-trace.jsonl').open('a') as output:
                    output.write(json.dumps(payload)+'\n')
            if diagnostics and payload.get('event') == 'client_fault':
                # Bounded numeric fault metadata only; no registers containing
                # pointers, memory contents, tickets or process command lines.
                fault = {key: payload[key] for key in ('rva', 'hresult')
                         if isinstance(payload.get(key), int) and 0 <= payload[key] <= 0xffffffff}
                if payload.get('type') in ('access-violation', 'illegal-instruction', 'abort', 'arithmetic', 'breakpoint'):
                    fault['type'] = payload['type']
                with (run/'client-faults.jsonl').open('a') as output:
                    output.write(json.dumps(fault)+'\n')
        script.on('message', agent_message)
        script.load()
        status = script.exports_sync.status()
        if status['failed'] or not status['armed']:
            raise RuntimeError('Private startup adapter rejected the client build')
        device.resume(pid)
        record('game_resumed')
        friends_feed = PrivateFriendsFeed(args.friends_snapshot)
        overlay_poll = 0
        history_poll = 0
        status_poll = 0
        while not detached.wait(.05 if overlay_feed else .5) and not (run/'stop.request').exists():
            try: friends_feed.poll(script)
            except Exception: record('private_friends_feed_failed')
            if time.monotonic() >= history_poll:
                save_history()
                history_poll = time.monotonic() + 1
            try:
                if time.monotonic() >= status_poll:
                    status = script.exports_sync.status()
                    status_poll = time.monotonic() + .5
            except frida.InvalidOperationError:
                if detached.wait(.5):
                    break
                raise
            if status['failed']:
                raise RuntimeError('Client startup validation failed; closing this private process')
            if overlay_feed:
                try:
                    for index, x, y, width, height, frame in overlay_feed.Next(script.exports_sync.overlayinput()):
                        script.post({'type': 'overlay-frame', 'index': index, 'x': x, 'y': y,
                                     'width': width, 'height': height}, frame)
                    if time.monotonic() >= overlay_poll:
                        overlay_status = script.exports_sync.overlaystatus()
                        (run/'overlay-status.json').write_text(json.dumps(overlay_status, indent=2)+'\n')
                        overlay_poll = time.monotonic() + 5
                        if overlay_status.get('failure'):
                            overlay_feed = None
                except Exception:
                    # Optional diagnostics never become a gameplay dependency.
                    record('overlay_feed_failed')
                    overlay_feed = None
            aim_request = run/'aim-trace.request'
            if aim_actor and aim_request.exists() and status['done']:
                aim_request.unlink()
                try:
                    script.exports_sync.aimtrace()
                except Exception:
                    record('aim_trace_failed')
            metadata = run/'packet-metadata.request'
            if metadata.exists() and status['done']:
                metadata.unlink()
                try:
                    rows = script.exports_sync.packetmetadata()
                    (run/'packet-metadata.json').write_text(json.dumps(rows, indent=2)+'\n')
                    record('packet_metadata_saved')
                except Exception:
                    record('packet_metadata_failed')
    finally:
        stopped.set()
        subprocess.run([proton.g_proton.wineserver_bin, '-k'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        save_history(final=True)
        if session:
            try:
                session.detach()
            except Exception:
                pass
        if device:
            manager.remove_remote_device('127.0.0.1:27042')
        if process:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for listener in listeners:
            listener.close()
        log.close()
        if addon_log:
            addon_log.close()


def check_installation(args):
    """No game, credentials or network connection: validate local prerequisites."""
    failures = []
    for command in ('ip', 'unshare', 'openssl'):
        if not shutil.which(command):
            failures.append('Missing system command: ' + command)
    for path in (args.game/'thedivision.exe', args.game/'uplay_r1_loader64.dll',
                 args.runtime/'proton', args.frida/'frida-server-17.4.0.exe',
                 HERE/'startup_agent.js', HERE/'private_friends_agent.js', HERE/'private_friends_feed.py', HERE/'tutorial_history.py', HERE/'overlay_frame.py'):
        if not path.is_file():
            failures.append('Missing file: ' + str(path))
    try:
        sys.path.insert(0, str(args.frida/'deps-17.4.0'))
        import frida
        if frida.__version__ != '17.4.0':
            failures.append('The adapter requires Python Frida 17.4.0.')
    except (ImportError, OSError) as error:
        failures.append('Cannot load Python Frida 17.4.0: ' + str(error))
    if shutil.which('unshare'):
        result = subprocess.run(['unshare', '--user', '--map-root-user', '--net', 'true'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            failures.append('Unprivileged user/network namespaces are unavailable: ' + result.stderr.strip()[:300])
    if failures:
        print('\n'.join(failures))
        return 1
    print('Linux/Proton launch dependencies are available.')
    return 0


def main():
    global STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--frida', type=Path, required=True)
    parser.add_argument('--state-directory', type=Path, default=HERE, help='Writable launcher data folder; separate from versioned adapter files')
    parser.add_argument('--check', action='store_true', help='Check local dependencies without launching or receiving credentials')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--addon-events', type=Path, help='Launcher-owned file for fixed client lifecycle event names only')
    parser.add_argument('--friends-snapshot', type=Path)
    add_display_arguments(parser)
    parser.add_argument('--overlay-snapshot', type=Path, help='Opt-in private D3D11 pose overlay; launcher snapshot file')
    args = parser.parse_args()
    STATE = args.state_directory.resolve()
    if args.check:
        raise SystemExit(check_installation(args))
    handoff = json.loads(sys.stdin.buffer.readline(65537))
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}', handoff['grant']):
        raise ValueError('Invalid private launch grant')
    args.game, args.runtime, args.frida = args.game.resolve(), args.runtime.resolve(), args.frida.resolve()
    if args.addon_events:
        args.addon_events = args.addon_events.resolve()
        if not args.addon_events.is_relative_to(STATE/'captures'):
            raise ValueError('Addon event file must be in launcher captures')
    if args.overlay_snapshot:
        args.overlay_snapshot = args.overlay_snapshot.resolve()
        if not args.overlay_snapshot.is_relative_to(STATE/'captures') or args.overlay_snapshot.name != 'overlay.txt':
            raise ValueError('Overlay snapshot must be a launcher-owned capture file')
    if args.worker:
        worker(args, handoff)
        return
    os.umask(0o077)
    run = STATE/'runtime'/uuid.uuid4().hex[:16]
    (run/'client').mkdir(parents=True, mode=0o700)
    for path in args.game.iterdir():
        if path.name.lower() in ('ww.vdf', 'uplay_install.manifest', 'uplay_manifest.xml'):
            continue
        if path.name.lower() in ('thedivision.exe', 'uplay_r1_loader64.dll'):
            shutil.copy2(path, run/'client'/path.name)
        else:
            (run/'client'/path.name).symlink_to(path, target_is_directory=path.is_dir())
    (run/'server.pem').write_text(handoff['certificatePem'])
    subprocess.run(['openssl', 'req', '-new', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
        '-nodes', '-days', '2', '-subj', '/CN=private-client', '-keyout', str(run/'client.key'), '-out', str(run/'client.pem')],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    endpoints = {entry['role']: (entry['host'], int(entry['port'])) for entry in handoff['endpoints']}
    if set(endpoints) != set(ROLES):
        raise ValueError('Incomplete game server endpoints')
    relay = socket.socket(socket.AF_UNIX)
    relay.bind(str(run/'relay.sock'))
    stopped = threading.Event()
    def connect(client):
        selector = client.recv(1)
        if len(selector) != 1 or selector[0] >= len(ROLES):
            raise OSError('Invalid relay selector')
        return socket.create_connection(endpoints[ROLES[selector[0]]], timeout=15)
    threading.Thread(target=listen, args=(relay, connect, stopped), daemon=True).start()
    print('PRIVATE CLIENT:', run, flush=True)
    child = None
    def stop(*_):
        (run/'stop.request').touch()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with (run/'runtime.log').open('wb') as output:
            command = ['unshare', '--user', '--map-root-user', '--net', '--pid', '--fork', '--kill-child=KILL',
                sys.executable, str(Path(__file__).resolve()), '--worker', str(run), '--game', str(args.game),
                '--runtime', str(args.runtime), '--frida', str(args.frida), '--state-directory', str(STATE)]
            if args.addon_events:
                command += ['--addon-events', str(args.addon_events)]
            if args.overlay_snapshot:
                command += ['--overlay-snapshot', str(args.overlay_snapshot)]
            if args.friends_snapshot:
                command += ['--friends-snapshot', str(args.friends_snapshot)]
            command += ['--display-mode', args.display_mode, '--display-width', str(args.display_width),
                        '--display-height', str(args.display_height)]
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=output)
            child.stdin.write(json.dumps(handoff).encode()+b'\n')
            child.stdin.close()
            handoff.clear()
            result = child.wait()
        if result:
            raise RuntimeError(f'Client startup failed; inspect {run / "runtime.log"}')
    finally:
        stopped.set()
        relay.close()
        (run/'relay.sock').unlink(missing_ok=True)
        (run/'client.key').unlink(missing_ok=True)
        # Namespace exit kills this prefix's children; no Steam/Ubisoft processes are targeted.
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=15)


if __name__ == '__main__':
    main()
