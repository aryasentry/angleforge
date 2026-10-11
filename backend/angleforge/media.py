"""Native media preparation. All public timestamps are relative to first video PTS."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid
import wave

import numpy as np


_analysis_locks_guard = threading.Lock()
_analysis_locks = {}


def binary(name):
    configured = os.getenv('ANGLEFORGE_' + name.upper())
    path = configured or shutil.which(name)
    if not path and (Path(__file__).resolve().parents[2] / '.tools' / 'bin' / name).is_file():
        path = str(Path(__file__).resolve().parents[2] / '.tools' / 'bin' / name)
    if not path and Path('/opt/homebrew/bin', name).is_file():
        path = str(Path('/opt/homebrew/bin', name))
    if not path:
        raise RuntimeError(f'{name} is missing. Install native FFmpeg and restart AngleForge.')
    return str(path)


def preflight():
    result = {'ok': False, 'ffmpeg': None, 'ffprobe': None, 'encoder': 'libx264', 'error': None}
    try:
        for name in ('ffmpeg', 'ffprobe'):
            proc = subprocess.run([binary(name), '-version'], capture_output=True, text=True, timeout=10, check=True)
            result[name] = proc.stdout.splitlines()[0]
        encoders = subprocess.run([binary('ffmpeg'), '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=10, check=True).stdout
        if 'libx264' not in encoders or ' aac ' not in encoders:
            raise RuntimeError('FFmpeg requires libx264 and AAC encoders')
        result['ok'] = True
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    return result


def probe(path, ctx):
    return json.loads(ctx.run([binary('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def _seconds(value, default=0.):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _fps(value):
    try:
        num, den = str(value).split('/')
        num, den = int(num), int(den)
        if num <= 0 or den <= 0:
            raise ValueError()
        return {'num': num, 'den': den}
    except (ValueError, TypeError):
        return {'num': 30, 'den': 1}


def stream_origin(stream):
    return round(_seconds(stream.get('start_time')) * 1_000_000)


def audio_normalize_filter(video_origin_us, audio_stream):
    delta = stream_origin(audio_stream) - video_origin_us
    filters = ['asetpts=PTS-STARTPTS']
    if delta < 0:
        filters += [f'atrim=start={-delta / 1_000_000:.6f}', 'asetpts=PTS-STARTPTS']
    elif delta > 0:
        filters += [f'adelay={delta / 1000:.3f}:all=1']
    return ','.join(filters)


def _waveform(path, bins=400, ctx=None):
    values = []
    with wave.open(str(path), 'rb') as audio:
        channels, count = audio.getnchannels(), audio.getnframes()
        if audio.getsampwidth() != 2:
            raise RuntimeError('Expected PCM16 waveform audio')
        bins = min(bins, max(1, count))
        for index in range(bins):
            if ctx:
                ctx.check_cancelled()
            size = count * (index + 1) // bins - count * index // bins
            chunk = np.frombuffer(audio.readframes(size), dtype='<i2').astype(np.float32) / 32768.
            if channels > 1:
                chunk = chunk.reshape(-1, channels).mean(axis=1)
            values.append(round(float(np.sqrt(np.mean(chunk ** 2))), 5) if chunk.size else 0.)
    return values


def prepare_asset(original: Path, derived: Path, asset_id: str, name: str, ctx):
    original, derived = Path(original), Path(derived)
    derived.mkdir(parents=True, exist_ok=True)
    ctx.progress(None, 'Inspecting media streams')
    metadata = probe(original, ctx)
    videos = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'audio']
    if not videos and not audios:
        raise ValueError('No decodable video or audio stream found')
    video = videos[0] if videos else None
    audio = audios[0] if audios else None
    primary = video or audio
    duration = _seconds(primary.get('duration')) or _seconds(metadata.get('format', {}).get('duration'))
    if duration <= 0 or duration > 24 * 3600:
        raise ValueError('Media duration is missing or outside the supported 24 hour limit')
    duration_us = round(duration * 1_000_000)
    origin_us = stream_origin(primary)
    rotation = 0
    if video:
        rotation = int(_seconds(video.get('tags', {}).get('rotate')))
        for side in video.get('side_data_list', []):
            if 'rotation' in side:
                rotation = round(_seconds(side['rotation']))
    width, height = (int(video.get('width', 0)), int(video.get('height', 0))) if video else (0, 0)
    if rotation % 180:
        width, height = height, width
    if video and (width < 2 or height < 2 or width * height > 100_000_000):
        raise ValueError('Unsupported or invalid video dimensions')
    digest = hashlib.sha256()
    with original.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            ctx.check_cancelled()
            digest.update(chunk)
    asset = dict(id=asset_id, name=name, kind='video' if video else 'audio', status='ready', duration_us=duration_us,
                 width=width, height=height, fps=_fps(primary.get('avg_frame_rate')), has_audio=bool(audio), rotation=0,
                 fingerprint=digest.hexdigest(), proxy={'source_origin_us': origin_us, 'timebase': 1_000_000}, waveform=[], error=None)
    # Only publish complete files. Temporary directories are reclaimed even on cancel.
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=derived) as staging:
        stage = Path(staging)
        common = [binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-xerror', '-y', '-threads', '2', '-i', str(original)]
        if audio:
            ctx.progress(None, 'Extracting audio and waveform')
            af = audio_normalize_filter(origin_us, audio) + f',apad,atrim=duration={duration:.6f}'
            ctx.run(common + ['-map', f'0:{audio["index"]}', '-vn', '-af', af, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(stage / 'audio.wav')], duration_us)
            asset['waveform'] = _waveform(stage / 'audio.wav', ctx=ctx)
            (stage / 'waveform.json').write_text(json.dumps(asset['waveform']))
            # Stereo monitor audio matches original-export channel semantics;
            # the mono16k WAV remains dedicated to synchronization/measurements.
            ctx.progress(None, 'Preparing stereo preview audio')
            layout = 'pan=stereo|c0=c0|c1=c0' if audio.get('channels') == 1 else 'aformat=channel_layouts=stereo'
            ctx.run(common + ['-map', f'0:{audio["index"]}', '-vn', '-af', af + ',aresample=48000,' + layout, '-ar', '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '160k', '-t', f'{duration:.6f}', '-movflags', '+faststart', str(stage / 'monitor.m4a')], duration_us)
            monitor = probe(stage / 'monitor.m4a', ctx)
            monitor_stream = next(s for s in monitor['streams'] if s['codec_type'] == 'audio')
            if monitor_stream.get('channels') != 2 or int(monitor_stream.get('sample_rate', 0)) != 48000:
                raise RuntimeError('Stereo preview audio validation failed')
        if video:
            ctx.progress(None, 'Generating editing proxy')
            vf = "setpts=PTS-STARTPTS,scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30"
            args = common + ['-map', f'0:{video["index"]}', '-vf', vf]
            if audio:
                args += ['-map', f'0:{audio["index"]}', '-af', audio_normalize_filter(origin_us, audio) + ',apad', '-c:a', 'aac', '-b:a', '96k']
            else:
                args += ['-an']
            args += ['-t', f'{duration:.6f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-threads', '2', '-pix_fmt', 'yuv420p', '-metadata:s:v:0', 'rotate=0', '-movflags', '+faststart', str(stage / 'proxy.mp4')]
            ctx.run(args, duration_us)
            ctx.run(common + ['-map', f'0:{video["index"]}', '-vf', 'setpts=PTS-STARTPTS,thumbnail=30,scale=320:320:force_original_aspect_ratio=decrease', '-frames:v', '1', '-update', '1', str(stage / 'thumbnail.jpg')])
            normalized = probe(stage / 'proxy.mp4', ctx)
            output_video = next(s for s in normalized['streams'] if s['codec_type'] == 'video')
            if abs(_seconds(output_video.get('start_time'))) > .05:
                raise RuntimeError('Proxy timestamp normalization failed')
            proxy_duration = _seconds(output_video.get('duration'))
            if proxy_duration <= 0 or abs(proxy_duration - duration) > .15:
                raise RuntimeError('Decoded proxy duration differs from declared source video duration')
            ctx.run([binary('ffmpeg'), '-v', 'error', '-xerror', '-threads', '2', '-i', str(stage / 'proxy.mp4'), '-f', 'null', '-'])
        metadata['normalization'] = {'source_origin_us': origin_us, 'source_rotation': rotation, 'proxy_fps': {'num': 30, 'den': 1}, 'timestamp_policy': 'first video frame is zero; source is time based, not frame based', 'variable_frame_rate_hint': bool(video and video.get('avg_frame_rate') != video.get('r_frame_rate'))}
        (stage / 'metadata.json').write_text(json.dumps(metadata))
        for entry in stage.iterdir():
            os.replace(entry, derived / entry.name)
    ctx.check_cancelled()
    return asset


def analyze_asset(original, derived, asset, ctx):
    """Serialize/cache analysis for an asset across local and frame-plan jobs."""
    key = str(Path(derived).resolve())
    with _analysis_locks_guard:
        lock = _analysis_locks.setdefault(key, threading.Lock())
    while not lock.acquire(timeout=.1):
        ctx.check_cancelled()
    try:
        ctx.check_cancelled()
        return _analyze_asset_locked(original, derived, asset, ctx)
    finally:
        lock.release()


def _analyze_asset_locked(original, derived, asset, ctx):
    """Local sample measurements only; no semantic claims or transcription."""
    derived = Path(derived)
    ctx.progress(None, 'Measuring local footage samples')
    cache = derived / 'analysis-v2.json'
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get('fingerprint') == asset['fingerprint']:
            return cached
    frames = []
    boundaries = []
    if asset['kind'] == 'video':
        # Decode normalized proxy samples, preserving their declared source times.
        raw = derived / ('.analysis-' + uuid.uuid4().hex + '.raw')
        try:
            ctx.run([binary('ffmpeg'), '-v', 'error', '-y', '-threads', '2', '-i', str(derived / 'proxy.mp4'), '-t', str(min(600, asset['duration_us'] / 1_000_000)), '-vf', 'fps=1,scale=160:90,format=gray', '-an', '-f', 'rawvideo', str(raw)])
            samples = np.fromfile(raw, dtype=np.uint8)
            samples = samples[:len(samples) // 14400 * 14400].reshape(-1, 90, 160)
            previous = None
            for index, sample in enumerate(samples):
                ctx.check_cancelled()
                diff = 0. if previous is None else float(np.mean(np.abs(sample.astype(float) - previous))) / 255.
                frames.append({'source_us': index * 1_000_000, 'mean_luma': round(float(sample.mean()) / 255., 4), 'contrast': round(float(sample.std()) / 255., 4), 'scene_change_score': round(diff, 4)})
                if diff > .25:
                    boundaries.append(index * 1_000_000)
                previous = sample.astype(float)
            # Six local contact frames available through the scoped media endpoint.
            for index in range(min(6, len(frames))):
                source_us = round(index * max(0, asset['duration_us'] - 100000) / max(1, min(6, len(frames)) - 1))
                filename = f'analysis-{index}.jpg'
                # A canceled frame extraction must not damage a prior contact JPEG.
                temporary_frame = derived / ('.analysis-' + uuid.uuid4().hex + '.jpg')
                try:
                    ctx.run([binary('ffmpeg'), '-v', 'error', '-y', '-threads', '2', '-ss', f'{source_us / 1_000_000:.6f}', '-i', str(derived / 'proxy.mp4'), '-vf', 'scale=320:320:force_original_aspect_ratio=decrease', '-frames:v', '1', '-update', '1', str(temporary_frame)])
                    ctx.check_cancelled()
                    os.replace(temporary_frame, derived / filename)
                finally:
                    temporary_frame.unlink(missing_ok=True)
                frame = frames[min(len(frames)-1, source_us // 1_000_000)]
                frame['image_url'] = f'/api/media/{asset["id"]}/analysis-{index}'
                frame['image_source_us'] = source_us
        finally:
            raw.unlink(missing_ok=True)
    audio_metrics = None
    if asset.get('has_audio'):
        total, squares, clipped, silent = 0, 0., 0, 0
        with wave.open(str(derived / 'audio.wav'), 'rb') as recording:
            while raw_samples := recording.readframes(16000 * 10):
                ctx.check_cancelled()
                samples = np.frombuffer(raw_samples, dtype='<i2').astype(float) / 32768.
                total += samples.size
                squares += float(np.sum(samples ** 2))
                clipped += int(np.count_nonzero(np.abs(samples) >= .99))
                silent += int(np.count_nonzero(np.abs(samples) < .005))
        audio_metrics = {'rms': round(math.sqrt(squares / total), 5) if total else 0., 'clipping_fraction': round(clipped / total, 5) if total else 0., 'silence_fraction': round(silent / total, 5) if total else 1.}
    result = {'asset_id': asset['id'], 'fingerprint': asset['fingerprint'], 'analysis_version': 2, 'method': 'local deterministic 1 fps luma samples and PCM16 levels', 'frames': frames, 'scene_boundaries_us': boundaries, 'audio': audio_metrics, 'transcription': {'status': 'unavailable', 'reason': 'No local speech model installed'}, 'warnings': ['One frame per second sampling can miss fast action; scene boundaries are coarse candidates.']}
    if asset['duration_us'] > 600_000_000:
        result['warnings'].append('Luma measurements cover the first ten minutes only; contact frames span the full source.')
    ctx.check_cancelled()
    temporary = derived / ('.analysis-' + uuid.uuid4().hex + '.json')
    try:
        temporary.write_text(json.dumps(result))
        ctx.check_cancelled()
        os.replace(temporary, cache)
    finally:
        temporary.unlink(missing_ok=True)
    return result
