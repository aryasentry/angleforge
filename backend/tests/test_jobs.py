import sys
import time
from angleforge.jobs import JobManager


def terminal(manager, ident):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = manager.get(ident)
        if job['status'] in ('succeeded', 'failed', 'canceled'):
            return job
        time.sleep(.03)
    raise AssertionError('job did not finish')


def test_result_survives_restart(tmp_path):
    manager = JobManager(tmp_path)
    job = manager.submit('test', lambda ctx: {'value': 42})
    assert terminal(manager, job['id'])['result'] == {'value': 42}
    manager.shutdown()
    reopened = JobManager(tmp_path)
    assert reopened.get(job['id'])['status'] == 'succeeded'
    reopened.shutdown()


def test_cancel_running_child(tmp_path):
    manager = JobManager(tmp_path, max_workers=1)
    job = manager.submit('sleep', lambda ctx: {'output': ctx.run([sys.executable, '-c', 'import time; time.sleep(60)'])})
    time.sleep(.15)
    manager.cancel(job['id'])
    assert terminal(manager, job['id'])['status'] == 'canceled'
    manager.shutdown()


def test_failure_and_bounded_queue(tmp_path):
    manager = JobManager(tmp_path, max_workers=1)
    job = manager.submit('failure', lambda ctx: ctx.run([sys.executable, '-c', 'raise ValueError("bad media")']))
    failed = terminal(manager, job['id'])
    assert failed['status'] == 'failed'
    assert 'bad media' in failed['error']
    manager.shutdown()


def test_failure_after_publish_restores_previous_output(tmp_path):
    manager = JobManager(tmp_path / 'jobs')
    target = tmp_path / 'export.mp4'
    target.write_bytes(b'previous')
    def render(ctx):
        temporary = tmp_path / 'partial.mp4'
        temporary.write_bytes(b'completed render')
        ctx.publish_output(temporary, target)
        raise RuntimeError('post publication integration failed')
    job = manager.submit('export', render)
    assert terminal(manager, job['id'])['status'] == 'failed'
    manager.shutdown()
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.previous-*'))


def test_recovery_marks_interrupted_job_failed(tmp_path):
    import json
    import sqlite3
    manager = JobManager(tmp_path)
    manager.shutdown()
    document = dict(id='interrupted', kind='export', status='running', progress=.5, message='rendering', result=None, error=None, created_at='then', updated_at='then')
    db = sqlite3.connect(tmp_path / 'jobs.sqlite3')
    db.execute('INSERT INTO jobs VALUES (?, ?)', ('interrupted', json.dumps(document)))
    db.commit()
    db.close()
    manager = JobManager(tmp_path)
    assert manager.get('interrupted')['status'] == 'failed'
    assert 'restart' in manager.get('interrupted')['error']
    manager.shutdown()


def test_cancel_cannot_win_after_atomic_publish(tmp_path):
    import threading
    manager = JobManager(tmp_path / 'jobs')
    target = tmp_path / 'export.mp4'
    published = threading.Event()
    def render(ctx):
        partial = tmp_path / 'partial.mp4'
        partial.write_bytes(b'verified')
        ctx.publish_output(partial, target)
        published.set()
        time.sleep(.1)
        return {'verified': True}
    job = manager.submit('export', render)
    assert published.wait(timeout=2)
    canceled = []
    thread = threading.Thread(target=lambda: canceled.append(manager.cancel(job['id'])))
    thread.start()
    thread.join(timeout=3)
    assert canceled[0]['status'] == 'succeeded'
    assert target.read_bytes() == b'verified'
    manager.shutdown()


def test_restart_cleans_only_owned_temporary_directories(tmp_path):
    staging = tmp_path / 'assets' / 'asset-id' / '.prepare-stale'
    staging.mkdir(parents=True)
    (staging / 'partial.mp4').write_bytes(b'partial')
    original = staging.parent / 'original.mp4'
    original.write_bytes(b'original')
    render = tmp_path / 'exports' / 'export-id' / '.render-stale'
    render.mkdir(parents=True)
    manager = JobManager(tmp_path)
    assert not staging.exists() and not render.exists()
    assert original.read_bytes() == b'original'
    manager.shutdown()
