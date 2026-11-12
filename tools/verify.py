#!/usr/bin/env python3
"""Client checks that do not require server source, content or a live account."""
import argparse
from pathlib import Path
import sys
from build import ROOT, PROJECT_ROOT, environment, run, sdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet')
    parser.add_argument('--packages', type=Path)
    parser.add_argument('--suite', choices=('all', 'launcher', 'remote', 'adapters', 'publication'), default='all')
    args = parser.parse_args()
    env = environment(args.packages)
    if args.suite in ('all', 'publication'):
        run([sys.executable, ROOT / 'tools/test_audit_publication.py'], env)
        run([sys.executable, ROOT / 'tools/test_audit_release.py'], env)
        run([sys.executable, ROOT / 'tools/audit_publication.py'], env)
    if args.suite in ('all', 'launcher', 'remote'):
        dotnet = sdk(args.dotnet)
        tests = PROJECT_ROOT / 'src/LauncherDesktop.Tests/bin/Debug/net10.0/LauncherDesktop.Tests.dll'
        if args.suite != 'remote':
            run([dotnet, tests, '--launcher-local', ROOT], env)
        if args.suite in ('all', 'remote'):
            run([dotnet, tests, '--remote-local'], env)
    if args.suite in ('all', 'adapters'):
        client = ROOT / 'adapters'
        for name in ('test_display_settings.py', 'test_tutorial_history.py', 'test_windows_client.py', 'test_private_friends_feed.py'):
            run([sys.executable, client / name], env, cwd=client)
        for name in ('test_aim_diagnostic.js', 'test_private_friends.js', 'test_windows_environment.js'):
            run(['node', client / name], env, cwd=client)


if __name__ == '__main__':
    main()
