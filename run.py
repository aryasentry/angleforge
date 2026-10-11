#!/usr/bin/env python3
"""Cross-platform setup/preflight/launcher. No shell command construction."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import webbrowser

ROOT=Path(__file__).resolve().parent
PYTHON=ROOT/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')


def run(args,**kwargs):
    subprocess.run([str(arg) for arg in args],check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',action='store_true',help='Install pinned Python/UI dependencies and build frontend')
    parser.add_argument('--preflight',action='store_true',help='Check dependencies without launching')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--open',action='store_true',help='Open editor in default browser')
    args=parser.parse_args()
    if not (3,11)<=sys.version_info[:2]<=(3,13):
        parser.error('Use Python 3.11, 3.12, or 3.13.')
    os.chdir(ROOT)
    if args.setup:
        if not PYTHON.exists(): run([sys.executable,'-m','venv',ROOT/'.venv'])
        run([PYTHON,'-m','pip','install','-r',ROOT/'requirements.lock.txt'])
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if npm is None: parser.error('Node.js 22.12+ (or 24 LTS) with npm is required to build the UI.')
        run([npm,'ci'],cwd=ROOT/'frontend')
        run([npm,'run','build'],cwd=ROOT/'frontend')
    if not PYTHON.exists(): parser.error('Run python run.py --setup first to prepare the local environment.')
    env={**os.environ,'PYTHONPATH':str(ROOT/'backend')}
    check=subprocess.run([str(PYTHON),'-c','from dotenv import load_dotenv; load_dotenv(); from angleforge.media import preflight; import json; print(json.dumps(preflight()))'],env=env,text=True,capture_output=True)
    if check.returncode:
        print('Python dependencies are incomplete. Run python run.py --setup.'); return 1
    status=json.loads(check.stdout)
    print(json.dumps(status,indent=2))
    if not status.get('ok',False):
        print('Install native FFmpeg and ffprobe, or set ANGLEFORGE_FFMPEG and ANGLEFORGE_FFPROBE in .env. See README.md.')
        return 1
    if not (ROOT/'frontend'/'dist'/'index.html').exists():
        print('Frontend build is missing. Run python run.py --setup.'); return 1
    if args.preflight: return 0
    url=f'http://127.0.0.1:{args.port}'
    print(f'AngleForge: {url}\nMedia and projects stay in the configured local data directory. Ctrl+C to stop.')
    # A short timer is only for opening the browser, never for job progress.
    if args.open:
        import threading
        threading.Timer(1.5,lambda:webbrowser.open(url)).start()
    command=[str(PYTHON),'-m','uvicorn','angleforge.app:create_app','--factory','--app-dir',str(ROOT/'backend'),'--host','127.0.0.1','--port',str(args.port)]
    process=subprocess.Popen(command,env=env)
    try: return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        return 0


if __name__=='__main__':
    raise SystemExit(main())
