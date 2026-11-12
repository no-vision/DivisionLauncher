#!/usr/bin/env python3
"""Prepare application-local adapters, keeping notices. Explicit network operation."""
from pathlib import Path
import argparse,hashlib,io,json,lzma,shutil,subprocess,sys,tempfile,urllib.request,zipfile
ROOT=Path(__file__).resolve().parents[1]
def fetch(row):
    with urllib.request.urlopen(row['url'],timeout=90) as response: data=response.read()
    if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Dependency SHA256 mismatch: '+row['url'])
    return data

def extract(data,target):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            if not (target/entry.filename).resolve().is_relative_to(target.resolve()):raise ValueError('Archive path escapes runtime')
        archive.extractall(target)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('platform',choices=['linux','windows']);a=p.parse_args()
    manifest=json.loads((ROOT/'dependencies'/f'{a.platform}.json').read_text())
    target=ROOT/'.cache/adapters'/a.platform
    if target.exists():raise ValueError('Destination already exists; prepare in a clean checkout or move it aside first')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        stage=Path(temporary)/'prepared';stage.mkdir()
        if a.platform=='windows':
            for i,row in enumerate(manifest):extract(fetch(row),stage/('python' if i==0 else 'python/Lib/site-packages'))
            (stage/'python/python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../client\n')
            (stage/'sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
        else:
            (stage/'frida-server-17.4.0.exe').write_bytes(lzma.decompress(fetch(manifest['fridaServer'])))
            subprocess.run([sys.executable,'-m','pip','install','--only-binary=:all:','--no-compile','--no-deps',
                '--target',str(stage/'deps-17.4.0'),*manifest['pythonPackages']],check=True)
        stage.rename(target)
    print('Prepared',target)
if __name__=='__main__':main()
