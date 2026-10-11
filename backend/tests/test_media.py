import json
from pathlib import Path
import subprocess
import threading
import wave

import numpy as np
import pytest

from angleforge.jobs import JobContext
from angleforge.media import prepare_asset, analyze_asset, binary, preflight, probe


class MemoryManager:
    def _update(self, ident, **changes):
        pass


def context():
    return JobContext(MemoryManager(), 'test', threading.Event())


@pytest.fixture(scope='module')
def fixture_files(tmp_path_factory):
    import importlib.util
    spec = importlib.util.spec_from_file_location('generate_fixtures', Path(__file__).resolve().parents[2] / 'scripts/generate_fixtures.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path_factory.mktemp('fixtures')
    module.generate(folder)
    return folder


def test_real_phone_like_proxy_and_local_analysis(fixture_files, tmp_path):
    assert preflight()['ok']
    original = fixture_files / 'synthetic_camera_C.mp4'
    asset = prepare_asset(original, tmp_path, 'camera-c', original.name, context())
    assert (asset['width'], asset['height']) == (360, 640)
    assert abs(asset['duration_us'] - 8_000_000) < 50000
    assert asset['has_audio'] and len(asset['waveform']) == 400
    assert all((tmp_path / name).is_file() for name in ['proxy.mp4', 'thumbnail.jpg', 'audio.wav', 'metadata.json'])
    proxy = probe(tmp_path / 'proxy.mp4', context())
    assert float(proxy['streams'][0]['start_time']) == 0
    analysis = analyze_asset(original, tmp_path, asset, context())
    assert analysis['transcription']['status'] == 'unavailable'
    assert analysis['frames'] and analysis['audio']['rms'] > .01
    assert (tmp_path / 'analysis-0.jpg').is_file()
    assert analyze_asset(original, tmp_path, asset, context()) == analysis


def test_reject_invalid_media(tmp_path):
    original = tmp_path / 'fake.mp4'
    original.write_bytes(b'not media')
    with pytest.raises(RuntimeError):
        prepare_asset(original, tmp_path / 'derived', 'x', 'fake.mp4', context())


def test_vfr_rotation_is_applied(fixture_files, tmp_path):
    irregular = tmp_path / 'vfr.mp4'
    rotated = tmp_path / 'rotated.mov'
    subprocess.run([binary('ffmpeg'), '-v', 'error', '-y', '-i', str(fixture_files / 'synthetic_camera_A.mp4'), '-vf', "select='if(lt(t,4),not(mod(n,2)),1)'", '-fps_mode', 'vfr', '-c:v', 'libx264', '-threads', '2', '-c:a', 'copy', str(irregular)], check=True)
    subprocess.run([binary('ffmpeg'), '-v', 'error', '-y', '-i', str(irregular), '-c', 'copy', '-metadata:s:v:0', 'rotate=90', str(rotated)], check=True)
    asset = prepare_asset(rotated, tmp_path / 'derived', 'rotated', 'rotated.mov', context())
    assert (asset['width'], asset['height']) == (360, 640)
    proxy = probe(tmp_path / 'derived/proxy.mp4', context())
    stream = next(s for s in proxy['streams'] if s['codec_type'] == 'video')
    assert (stream['width'], stream['height']) == (360, 640)
    assert stream['avg_frame_rate'] == '30/1'
    private = json.loads((tmp_path / 'derived/metadata.json').read_text())
    assert private['normalization']['source_rotation'] != 0
    assert private['normalization']['variable_frame_rate_hint']


def test_audio_start_is_aligned_to_video(tmp_path):
    original = tmp_path / 'late-audio.mp4'
    subprocess.run([binary('ffmpeg'), '-v', 'error', '-y', '-copyts', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=30:d=2', '-f', 'lavfi', '-i', 'sine=frequency=750:sample_rate=16000:duration=1.8', '-filter_complex', '[0:v]setpts=PTS+2/TB[v];[1:a]asetpts=PTS+2.2/TB[a]', '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', str(original)], check=True)
    asset = prepare_asset(original, tmp_path / 'derived', 'late', original.name, context())
    assert asset['proxy']['source_origin_us'] == 2_000_000
    with wave.open(str(tmp_path / 'derived/audio.wav'), 'rb') as recording:
        samples = np.frombuffer(recording.readframes(recording.getnframes()), dtype='<i2')
    onset = int(np.flatnonzero(np.abs(samples) > 400)[0]) / 16000
    assert onset == pytest.approx(.2, abs=.015)


def test_concurrent_analysis_reuses_atomic_cache(fixture_files, tmp_path, monkeypatch):
    import concurrent.futures
    import time
    from angleforge.jobs import JobContext
    original = fixture_files / 'synthetic_camera_A.mp4'
    asset = prepare_asset(original, tmp_path, 'camera-a', original.name, context())
    initial_run = JobContext.run
    count = []
    count_lock = threading.Lock()
    def counted_run(self, args, duration_us=None):
        if any(arg.startswith('fps=1,') for arg in args):
            with count_lock:
                count.append(1)
            time.sleep(.05)
        return initial_run(self, args, duration_us)
    monkeypatch.setattr(JobContext, 'run', counted_run)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(analyze_asset, original, tmp_path, asset, context())
        second = pool.submit(analyze_asset, original, tmp_path, asset, context())
        one, two = first.result(timeout=15), second.result(timeout=15)
    assert one == two
    assert len(count) == 1
    assert json.loads((tmp_path / 'analysis-v2.json').read_text()) == one
    assert len([frame for frame in one['frames'] if 'image_source_us' in frame]) == 6
    assert all((tmp_path / f'analysis-{index}.jpg').is_file() for index in range(6))
    assert not list(tmp_path.glob('.analysis-*'))


def test_waiting_analysis_can_be_canceled(tmp_path):
    import concurrent.futures
    from angleforge.jobs import JobCancelled
    from angleforge import media
    key = str(tmp_path.resolve())
    lock = threading.Lock()
    lock.acquire()
    with media._analysis_locks_guard:
        media._analysis_locks[key] = lock
    ctx = context()
    ctx.event.set()
    with pytest.raises(JobCancelled):
        analyze_asset(tmp_path / 'missing.mp4', tmp_path, {'id': 'pending'}, ctx)
    lock.release()


def decode_monitor(path):
    raw = subprocess.run([binary('ffmpeg'), '-v', 'error', '-i', str(path), '-map', '0:a:0', '-ac', '2', '-ar', '48000', '-f', 's16le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype='<i2').astype(float).reshape(-1, 2)


def test_mono_monitor_duplicates_channels_without_gain_drop(fixture_files, tmp_path):
    original = fixture_files / 'synthetic_camera_A.mp4'
    prepare_asset(original, tmp_path, 'monitor', original.name, context())
    monitor = decode_monitor(tmp_path / 'monitor.m4a')
    # Duplicated channels preserve loudness instead of the default -3dB upmix.
    assert np.corrcoef(monitor[:, 0], monitor[:, 1])[0, 1] > .999
    metadata = probe(tmp_path / 'monitor.m4a', context())
    assert metadata['streams'][0]['sample_rate'] == '48000'
    assert metadata['streams'][0]['channels'] == 2
    raw = subprocess.run([binary('ffmpeg'), '-v', 'error', '-i', str(tmp_path / 'monitor.m4a'), '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True, check=True).stdout
    downmix = np.frombuffer(raw, dtype='<i2').astype(float)
    with wave.open(str(tmp_path / 'audio.wav'), 'rb') as recording:
        reference = np.frombuffer(recording.readframes(recording.getnframes()), dtype='<i2').astype(float)
    size = min(len(reference), len(downmix))
    assert np.corrcoef(reference[:size], downmix[:size])[0, 1] > .95
    assert np.linalg.norm(downmix[:size]) / np.linalg.norm(reference[:size]) == pytest.approx(1, rel=.12)


def test_stereo_monitor_preserves_independent_channels(tmp_path):
    rate = 48000
    t = np.arange(rate * 2) / rate
    samples = np.column_stack((.15 * np.sin(2*np.pi*600*t), .05 * np.sin(2*np.pi*950*t)))
    original = tmp_path / 'stereo.wav'
    with wave.open(str(original), 'wb') as recording:
        recording.setnchannels(2)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes((samples*32767).astype('<i2').tobytes())
    asset = prepare_asset(original, tmp_path / 'derived', 'stereo', original.name, context())
    assert asset['kind'] == 'audio'
    monitor = decode_monitor(tmp_path / 'derived/monitor.m4a')
    rms = np.sqrt(np.mean(monitor[4800:80000] ** 2, axis=0))
    assert rms[0] / rms[1] == pytest.approx(3, rel=.08)
    assert abs(np.corrcoef(monitor[4800:80000, 0], monitor[4800:80000, 1])[0, 1]) < .05
