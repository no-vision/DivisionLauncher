"""Compatibility entry point for the pinned dependency preparer."""
from pathlib import Path
import subprocess,sys
if __name__ == '__main__':
    raise SystemExit(subprocess.call([sys.executable,str(Path(__file__).resolve().parent/'dependencies.py'),'windows']))
