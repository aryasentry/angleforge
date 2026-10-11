from copy import deepcopy
from pathlib import Path
import subprocess
import threading
import time
import uuid
import wave

import numpy as np
import pytest

from angleforge.exporter import render_project
from angleforge.jobs import JobManager
from angleforge.media import binary, prepare_asset
from test_media import context, fixture_files


def project_for(fixtures, tmp_path):
    assets = {}
    originals = {}
    for name in ('A', 'B', 'C'):
        original = fixtures / f'synthetic_camera_{name}.mp4'
        assets[name] = prepare_asset(original, tmp_path / name, name, original.name, context())
        originals[name] = original
    segments = [dict(id='one', asset_id='A', source_in_us=0, source_out_us=2_000_000, output_in_us=0, event_in_us=0, crop={'mode': 'fit', 'x': .5, 'y': .5}), dict(id='two', asset_id='B', source_in_us=1_000_000, source_out_us=3_000_000, output_in_us=2_000_000, event_in_us=3_000_000, crop={'mode': 'fill', 'x': .2, 'y': .5})]
    return dict(revision=7, settings={'width': 320, 'height': 180, 'fps': {'num': 30, 'den': 1}}, assets=assets, timeline={'video': segments, 'audio': [dict(id='audio', asset_id='A', source_in_us=0, source_out_us=4_000_000, output_in_us=0, gain=.5, muted=False, fade_in_us=200000, fade_out_us=200000)], 'music': [dict(id='music', asset_id='C', source_in_us=0, source_out_us=1_000_000, output_in_us=2_000_000, gain=.1)], 'captions': []}), originals


def frame_rgb(path, seconds):
    raw = subprocess.run([binary('ffmpeg'), '-v', 'error', '-ss', str(seconds), '-i', str(path), '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw[:3], dtype=np.uint8).astype(int)


def test_original_media_cut_crop_audio_and_verified_publish(fixture_files, tmp_path):
    project, originals = project_for(fixture_files, tmp_path)
    destination = tmp_path / 'edited.mp4'
    result = render_project(project, originals.__getitem__, destination, context())
    assert result['verified'] and result['revision'] == 7
    assert abs(result['duration_us'] - 4_000_000) <= 50000
    assert destination.is_file()
    assert not list(tmp_path.glob('.render-*'))
    decoded = subprocess.run([binary('ffmpeg'), '-v', 'error', '-i', str(destination), '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True, check=True).stdout
    rendered_audio = np.frombuffer(decoded, dtype='<i2').astype(float)
    with wave.open(str(tmp_path / 'A/audio.wav'), 'rb') as source_audio:
        original_audio = np.frombuffer(source_audio.readframes(source_audio.getnframes()), dtype='<i2').astype(float)
    # Away from fades/music, master waveform mapping and 0.5 gain must survive
    # AAC re-encoding within a small codec tolerance.
    start, end = 4000, 24000
    expected = original_audio[start:end] * .5
    measured = rendered_audio[start:end]
    assert np.corrcoef(expected, measured)[0, 1] > .95
    assert np.linalg.norm(measured) / np.linalg.norm(expected) == pytest.approx(1, rel=.15)
    # Blue camera A precedes warm camera B: validate the actual cut visually.
    before = frame_rgb(destination, 1.5)
    after = frame_rgb(destination, 2.5)
    assert before[2] > before[0]
    assert after[0] > after[2]
    portrait = deepcopy(project)
    portrait['settings'].update(width=180, height=320)
    portrait['timeline']['video'][1]['asset_id'] = 'C'
    result = render_project(portrait, originals.__getitem__, tmp_path / 'portrait.mp4', context())
    assert (result['width'], result['height']) == (180, 320)


def test_bad_source_and_captions_never_publish(fixture_files, tmp_path):
    project, originals = project_for(fixture_files, tmp_path)
    project['timeline']['captions'] = [{'id': 'caption', 'start_us': 0, 'end_us': 1_000_000, 'text': 'hello'}]
    destination = tmp_path / 'bad.mp4'
    with pytest.raises(ValueError, match='Caption export'):
        render_project(project, originals.__getitem__, destination, context())
    assert not destination.exists()
    project['timeline']['captions'] = []
    project['timeline']['video'][0]['source_out_us'] = 200_000_000
    with pytest.raises(ValueError):
        render_project(project, originals.__getitem__, destination, context())
    assert not destination.exists()


def test_cancel_render_preserves_existing_destination(fixture_files, tmp_path):
    project, originals = project_for(fixture_files, tmp_path)
    project['settings'].update(width=1920, height=1080)
    # Repeat event, giving cancellation time to interrupt a real render process.
    project['timeline']['video'] = []
    for index in range(12):
        project['timeline']['video'].append(dict(id=str(index), asset_id='A', source_in_us=0, source_out_us=12_000_000, output_in_us=index*12_000_000, event_in_us=0, crop={'mode': 'fill', 'x': .5, 'y': .5}))
    destination = tmp_path / 'prior.mp4'
    destination.write_bytes(b'prior completed export')
    manager = JobManager(tmp_path / 'jobs')
    job = manager.submit('export', lambda ctx: render_project(project, originals.__getitem__, destination, ctx))
    deadline = time.monotonic() + 5
    while manager.get(job['id'])['status'] == 'queued' and time.monotonic() < deadline:
        time.sleep(.01)
    time.sleep(.2)
    manager.cancel(job['id'])
    manager.shutdown()
    assert destination.read_bytes() == b'prior completed export'
    assert not list(tmp_path.glob('.render-*'))


def test_master_audio_cuts_preserve_sample_mapping_and_fades(fixture_files, tmp_path):
    project, originals = project_for(fixture_files, tmp_path)
    project['timeline']['music'] = []
    project['timeline']['audio'] = [dict(id='cut1', asset_id='A', source_in_us=400000, source_out_us=1400000, output_in_us=0, gain=.6, muted=False, fade_in_us=250000, fade_out_us=0), dict(id='cut2', asset_id='A', source_in_us=4200000, source_out_us=5200000, output_in_us=2000000, gain=.8, muted=False, fade_in_us=0, fade_out_us=250000)]
    destination = tmp_path / 'audio-cuts.mp4'
    render_project(project, originals.__getitem__, destination, context())
    decoded = subprocess.run([binary('ffmpeg'), '-v', 'error', '-i', str(destination), '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True, check=True).stdout
    measured = np.frombuffer(decoded, dtype='<i2').astype(float)
    with wave.open(str(tmp_path / 'A/audio.wav'), 'rb') as source:
        original = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(float)
    expected = np.zeros(64000)
    expected[:16000] = original[6400:22400] * .6
    expected[:4000] *= np.arange(4000) / 4000
    expected[32000:48000] = original[67200:83200] * .8
    expected[44000:48000] *= 1 - np.arange(4000) / 4000
    assert np.corrcoef(expected, measured[:64000])[0, 1] > .95
    assert np.linalg.norm(measured[:64000]) / np.linalg.norm(expected) == pytest.approx(1, rel=.12)
    # Gap between master segments must be actual silence (allow AAC ringing).
    assert np.sqrt(np.mean(measured[19000:30000] ** 2)) < 5
