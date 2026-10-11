"""Durable bounded local workers with cooperative and subprocess cancellation."""
from __future__ import annotations

import collections
import concurrent.futures
import json
import os
from pathlib import Path
import queue
import signal
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone


class JobCancelled(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            if process.poll() is None:
                process.terminate()
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=2)
        except ProcessLookupError:
            pass


class JobContext:
    def __init__(self, manager, ident, event):
        self.manager, self.id, self.event = manager, ident, event
        self.process = None
        self.lock = threading.Lock()
        self.publications = []
        self.publication_lock_held = False

    def check_cancelled(self):
        if self.event.is_set():
            raise JobCancelled('Canceled by user')

    def progress(self, value, message=''):
        self.check_cancelled()
        self.manager._update(self.id, progress=None if value is None else max(0., min(1., float(value))), message=message)

    def terminate(self):
        self.event.set()
        with self.lock:
            if self.process is not None:
                _terminate(self.process)

    def publish_output(self, temporary: Path, destination: Path):
        """Publish while excluding cancel through final job commit.

        Worker-owned contexts retain the finalization lock until _execute records
        success or rolls back. Direct callers still get atomic replacement.
        """
        if not hasattr(self.manager, 'contexts'):
            self.check_cancelled()
            os.replace(temporary, destination)
            return
        if not self.publication_lock_held:
            self.manager.lock.acquire()
            self.publication_lock_held = True
        self.check_cancelled()
        backup = None
        if destination.exists():
            backup = destination.parent / ('.previous-' + self.id + '-' + uuid.uuid4().hex)
            try:
                os.link(destination, backup)
            except OSError:
                shutil.copy2(destination, backup)
        try:
            os.replace(temporary, destination)
        except BaseException:
            if backup:
                backup.unlink(missing_ok=True)
            raise
        self.publications.append((destination, backup))

    def _finish_publications(self, success):
        try:
            for destination, backup in reversed(self.publications):
                if success:
                    if backup:
                        backup.unlink(missing_ok=True)
                elif backup:
                    os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            self.publications.clear()
        finally:
            if self.publication_lock_held:
                self.publication_lock_held = False
                self.manager.lock.release()

    def run(self, args: list[str], duration_us: int | None = None) -> str:
        self.check_cancelled()
        if not args or any(not isinstance(arg, str) for arg in args):
            raise ValueError('Process arguments must be a nonempty string array')
        command = list(args)
        ffmpeg = Path(command[0]).stem.lower() == 'ffmpeg'
        if ffmpeg and '-progress' not in command:
            command[1:1] = ['-progress', 'pipe:1', '-nostats']
        options = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        with self.lock:
            self.check_cancelled()
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', **options)
            self.process = proc
        lines = queue.Queue()
        stderr = collections.deque(maxlen=256)
        stdout = []
        def read_stdout():
            for line in proc.stdout:
                lines.put(line)
        def read_stderr():
            for line in proc.stderr:
                stderr.append(line[:2048])
        readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        started = time.monotonic()
        last_progress = 0.
        try:
            while proc.poll() is None or any(reader.is_alive() for reader in readers) or not lines.empty():
                self.check_cancelled()
                if time.monotonic() - started > 7200:
                    raise RuntimeError('Media process exceeded the two hour safety limit')
                try:
                    line = lines.get(timeout=.1)
                except queue.Empty:
                    continue
                if len(stdout) < 100000:
                    stdout.append(line)
                if ffmpeg and duration_us and line.startswith('out_time_us='):
                    try:
                        value = int(line.partition('=')[2]) / duration_us
                        if time.monotonic() - last_progress > .2:
                            self.progress(min(.99, value), 'Processing media')
                            last_progress = time.monotonic()
                    except ValueError:
                        pass
            code = proc.wait()
            self.check_cancelled()
            if code:
                raise RuntimeError(f'{Path(command[0]).name} exited with code {code}: ' + ''.join(stderr)[-12000:])
            return ''.join(stdout)
        finally:
            if proc.poll() is None:
                _terminate(proc)
            for reader in readers:
                reader.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            with self.lock:
                self.process = None


class JobManager:
    def __init__(self, data_dir: Path, max_workers=2):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(data_dir / 'jobs.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        # Startup owns only these private generated temporary directories.
        # Never remove original/finished media or user-selected locations.
        for collection in ('assets', 'exports'):
            root = data_dir / collection
            if root.exists():
                for directory in root.glob('*'):
                    if directory.is_dir() and not directory.is_symlink():
                        for prefix in ('.prepare-', '.render-'):
                            for stale in directory.glob(prefix + '*'):
                                if stale.is_dir() and not stale.is_symlink():
                                    shutil.rmtree(stale)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='angleforge')
        self.contexts = {}
        self.futures = {}
        self.closed = False
        for job in self.list():
            if job['status'] in ('queued', 'running'):
                self._update(job['id'], status='failed', error='Interrupted by application restart; retry the operation', message='Interrupted by restart')

    def _update(self, ident, **changes):
        with self.lock:
            job = self.get(ident)
            job.update(changes, updated_at=_now())
            self.db.execute('UPDATE jobs SET document=? WHERE id=?', (json.dumps(job), ident))
            self.db.commit()
            return job

    def submit(self, kind, fn):
        ident = str(uuid.uuid4())
        now = _now()
        job = dict(id=ident, kind=kind, status='queued', progress=None, message='Queued', result=None, error=None, created_at=now, updated_at=now)
        with self.lock:
            if self.closed:
                raise RuntimeError('Worker manager is shut down')
            # Bound pending work as well as active native processes.
            if len(self.contexts) >= 64:
                raise ValueError('Job queue is full; wait for existing jobs')
            self.db.execute('INSERT INTO jobs VALUES (?,?)', (ident, json.dumps(job)))
            self.db.commit()
            ctx = JobContext(self, ident, threading.Event())
            self.contexts[ident] = ctx
            self.futures[ident] = self.pool.submit(self._execute, ctx, fn)
        return job

    def _execute(self, ctx, fn):
        succeeded = False
        try:
            ctx.check_cancelled()
            self._update(ctx.id, status='running', message='Starting')
            result = fn(ctx)
            # Completion and cancel are serialized to prevent a late result winning.
            with self.lock:
                ctx.check_cancelled()
                self._update(ctx.id, status='succeeded', progress=1., result=result, message='Completed')
                succeeded = True
        except JobCancelled:
            self._update(ctx.id, status='canceled', message='Canceled', error=None)
        except Exception as error:
            self._update(ctx.id, status='canceled' if ctx.event.is_set() else 'failed', message='Canceled' if ctx.event.is_set() else 'Failed', error=None if ctx.event.is_set() else str(error)[-12000:])
        finally:
            ctx._finish_publications(succeeded)
            with self.lock:
                self.contexts.pop(ctx.id, None)
                self.futures.pop(ctx.id, None)

    def get(self, ident):
        with self.lock:
            row = self.db.execute('SELECT document FROM jobs WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            return json.loads(row[0])

    def list(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT document FROM jobs ORDER BY rowid DESC')]

    def cancel(self, ident):
        with self.lock:
            job = self.get(ident)
            if job['status'] in ('succeeded', 'failed', 'canceled'):
                return job
            ctx = self.contexts.get(ident)
            if ctx:
                ctx.event.set()
            self._update(ident, status='canceled', message='Canceled')
        if ctx:
            ctx.terminate()
        return self.get(ident)

    def shutdown(self):
        with self.lock:
            self.closed = True
            contexts = list(self.contexts.values())
        for ctx in contexts:
            self.cancel(ctx.id)
        self.pool.shutdown(wait=True, cancel_futures=False)
        with self.lock:
            self.db.close()
