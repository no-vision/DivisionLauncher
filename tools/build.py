#!/usr/bin/env python3
"""Build/publish the complete integrated source; outputs never enter live instances."""
from pathlib import Path
import argparse,os,shutil,subprocess,sys,json
ROOT=Path(__file__).resolve().parents[1]
PROJECT_ROOT=ROOT

def environment(packages=None):
    env=os.environ.copy()
    env.update(DOTNET_CLI_HOME=str(ROOT/'.cache/dotnet-home'),DOTNET_NOLOGO='1',
               DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1')
    runtime=ROOT/'.cache/runtime';runtime.mkdir(parents=True,exist_ok=True)
    if os.name!='nt': runtime.chmod(0o700);env['XDG_RUNTIME_DIR']=str(runtime)
    env['NUGET_PACKAGES']=str(Path(packages or env.get('NUGET_PACKAGES',ROOT/'.cache/nuget')).resolve())
    return env

def sdk(value=None):
    value=value or os.environ.get('DIVISION_DOTNET') or shutil.which('dotnet')
    if not value:
        local=PROJECT_ROOT/'.tools/dotnet'/('dotnet.exe' if os.name=='nt' else 'dotnet')
        if local.is_file(): value=str(local)
    if not value: raise ValueError('Install SDK from global.json, set DIVISION_DOTNET, or pass --dotnet.')
    return str(Path(value).resolve()) if Path(value).is_file() else value

def run(command,env=None,cwd=ROOT):
    print('+',' '.join(map(str,command)),flush=True)
    subprocess.run(list(map(str,command)),cwd=cwd,env=env,check=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['restore','build','publish-launcher','bundle-launcher'])
    p.add_argument('--runtime',choices=['linux-x64','win-x64'],default='linux-x64')
    p.add_argument('--configuration',choices=['Debug','Release'],default='Debug')
    p.add_argument('--dotnet');p.add_argument('--packages',type=Path);p.add_argument('--offline',action='store_true')
    p.add_argument('--version',default='0.4.16')
    p.add_argument('--refresh-locks',action='store_true',help='Explicitly regenerate dependency locks after project changes')
    p.add_argument('--adapter-source',type=Path,default=ROOT/'.cache/adapters/linux')
    a=p.parse_args();env=environment(a.packages);dotnet=sdk(a.dotnet)
    (ROOT/'.cache/empty-feed').mkdir(parents=True,exist_ok=True)
    restore=['--force-evaluate' if a.refresh_locks else '--locked-mode','--disable-parallel','-m:1','-nr:false']
    if a.offline: restore+=['--source',str(ROOT/'.cache/empty-feed'),'-p:NuGetAudit=false']
    if a.action in ('restore','build'):
        run([dotnet,'restore',PROJECT_ROOT/'DivisionLauncher.slnx',*restore],env)
        if a.action=='build':run([dotnet,'build',PROJECT_ROOT/'DivisionLauncher.slnx','--no-restore','-c',a.configuration,'-m:1','-nr:false','-v','minimal'],env)
        return
    output=ROOT/'artifacts'/'launcher'/a.runtime
    if a.action!='bundle-launcher':
        projects=('LauncherDesktop',)
        if a.runtime=='win-x64' and a.action=='publish-launcher' and not (ROOT/'.cache/adapters/windows/python/python.exe').is_file():
            raise ValueError('Prepare Windows adapter first: python tools/dependencies.py windows')
        for name in projects:
            project=PROJECT_ROOT/'src'/name/(name+'.csproj')
            run([dotnet,'restore',project,'-r',a.runtime,'-p:RepositoryRuntime='+a.runtime,'-p:SelfContained=true',*restore],env)
            target=output
            run([dotnet,'publish',project,'--no-restore','-c','Release','-r',a.runtime,
                 '--self-contained','true','-p:RepositoryRuntime='+a.runtime,'-o',target,'-m:1','-nr:false','-v','minimal'],env)
            if name=='LauncherDesktop':
                # A reused output directory can retain symbols from an older build.
                (target/'LauncherDesktop.pdb').unlink(missing_ok=True)
            run([sys.executable,ROOT/'tools/collect_notices.py',target,env['NUGET_PACKAGES'],'--assembly',name],env)
        return
    dest=ROOT/'artifacts/bundles';dest.mkdir(parents=True,exist_ok=True)
    if a.runtime=='linux-x64':
        run([sys.executable,ROOT/'tools/package_client.py',output,a.version,
             dest/f'DivisionPrivateLauncher-{a.version}-linux-x64.tar.gz','--adapter-source',a.adapter_source],env)
    else:
        run([sys.executable,ROOT/'tools/package_windows.py',output,a.version,
             dest/f'DivisionPrivateLauncher-{a.version}-win-x64.zip'],env)

if __name__=='__main__':
    try:main()
    except (ValueError,subprocess.CalledProcessError) as e:raise SystemExit(str(e))
