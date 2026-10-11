"""Compile canonical hard cuts, crop, and timed audio into a verified MP4."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from .media import binary, probe, audio_normalize_filter, _seconds


def _time(microseconds):
    return f'{microseconds / 1_000_000:.6f}'


def render_project(project: dict, resolve_original, destination: Path, ctx):
    video = project['timeline']['video']
    if not video:
        raise ValueError('Add at least one video segment before exporting')
    if project['timeline'].get('captions'):
        raise ValueError('Caption export is unavailable in this release; remove captions before rendering. No captions were silently omitted.')
    settings = project['settings']
    width, height = int(settings['width']), int(settings['height'])
    fps = settings['fps']
    if width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError('MP4 output dimensions must be positive even integers')
    if not (1 <= fps['num'] / fps['den'] <= 120):
        raise ValueError('Output frame rate is unsupported')
    duration_us = 0
    for segment in video:
        if segment['output_in_us'] != duration_us:
            raise ValueError('Export video segments must be contiguous and sorted')
        duration_us += segment['source_out_us'] - segment['source_in_us']
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    audio_segments = [s for s in project['timeline'].get('audio', []) + project['timeline'].get('music', []) if not s.get('muted') and s.get('gain', 1) > 0]
    originals = {}
    metadata = {}
    for segment in video + audio_segments:
        ident = segment['asset_id']
        asset = project['assets'].get(ident)
        if asset is None or asset.get('status') != 'ready':
            raise ValueError(f'Asset {ident} is missing or not ready')
        if not (0 <= segment['source_in_us'] < segment['source_out_us'] <= asset['duration_us']):
            raise ValueError(f'Asset {asset["name"]} has an out-of-range source interval')
        if ident not in originals:
            original = Path(resolve_original(ident))
            if not original.is_file():
                raise ValueError(f'Original media is missing for {asset["name"]}')
            originals[ident] = original
            metadata[ident] = probe(original, ctx)
    # Each source use has its own decoder, making trims deterministic even when
    # a source appears repeatedly or overlapping music uses that same asset.
    command = [binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '2']
    uses = video + audio_segments
    for segment in uses:
        command += ['-threads', '2', '-i', str(originals[segment['asset_id']])]
    filters = []
    visible_indices = []
    frame_scale = 1_000_000 * fps['den']
    def ceil_frame(time_us):
        return (time_us * fps['num'] + frame_scale - 1) // frame_scale
    for index, segment in enumerate(video):
        asset = project['assets'][segment['asset_id']]
        if asset['kind'] != 'video':
            raise ValueError('Audio-only assets cannot be placed on the video track')
        streams = [s for s in metadata[asset['id']]['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
        if not streams:
            raise ValueError('Original video stream is unavailable')
        crop = segment.get('crop', {'mode': 'fit', 'x': .5, 'y': .5})
        output_start_frame = ceil_frame(segment['output_in_us'])
        output_end_us = segment['output_in_us'] + segment['source_out_us'] - segment['source_in_us']
        count = ceil_frame(output_end_us) - output_start_frame
        if count == 0:
            continue
        visible_indices.append(index)
        # Quantize against the global output clock, never each clip separately.
        # Round source sampling downward so seeking displays its held frame.
        source_frame = (segment['source_in_us'] - segment['output_in_us']) * fps['num'] // frame_scale + output_start_frame
        trim_start = source_frame * fps['den'] / fps['num']
        base = f'[{index}:{streams[0]["index"]}]setpts=PTS-STARTPTS,fps={fps["num"]}/{fps["den"]}:round=up,trim=start={trim_start:.9f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,trim=end_frame={count},settb=AVTB'
        if crop['mode'] == 'fit':
            transform = f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black'
        elif crop['mode'] == 'fill':
            x, y = float(crop.get('x', .5)), float(crop.get('y', .5))
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError('Crop position must be between zero and one')
            transform = f'scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={width}:{height}:(iw-ow)*{x}:(ih-oh)*{y}'
        else:
            raise ValueError('Unsupported crop mode')
        filters.append(base + ',' + transform + f',setsar=1[v{index}]')
    labels = ''.join(f'[v{index}]' for index in visible_indices)
    filters.append(labels + f'concat=n={len(visible_indices)}:v=1:a=0,setpts=N*{fps["den"]}/({fps["num"]}*TB),format=yuv420p[vout]')
    filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={_time(duration_us)}[silence]')
    audio_labels = ['[silence]']
    for relative, segment in enumerate(audio_segments):
        index = len(video) + relative
        asset = project['assets'][segment['asset_id']]
        audios = [s for s in metadata[asset['id']]['streams'] if s['codec_type'] == 'audio']
        if not audios:
            raise ValueError(f'Original audio stream is unavailable in {asset["name"]}')
        duration = segment['source_out_us'] - segment['source_in_us']
        origin_us = asset.get('proxy', {}).get('source_origin_us', 0)
        chain = f'[{index}:{audios[0]["index"]}]' + audio_normalize_filter(origin_us, audios[0])
        layout = 'pan=stereo|c0=c0|c1=c0' if audios[0].get('channels') == 1 else 'aformat=channel_layouts=stereo'
        chain += f',atrim=start={_time(segment["source_in_us"])}:end={_time(segment["source_out_us"])},asetpts=PTS-STARTPTS,aresample=48000,{layout},volume={float(segment.get("gain", 1))}'
        fade_in = min(duration, max(0, segment.get('fade_in_us', 0)))
        fade_out = min(duration, max(0, segment.get('fade_out_us', 0)))
        if fade_in:
            chain += f',afade=t=in:st=0:d={_time(fade_in)}'
        if fade_out:
            chain += f',afade=t=out:st={_time(duration-fade_out)}:d={_time(fade_out)}'
        delay = round(segment['output_in_us'] * 48000 / 1_000_000)
        chain += f',adelay={delay}S:all=1[a{relative}]'
        filters.append(chain)
        audio_labels.append(f'[a{relative}]')
    filters.append(''.join(audio_labels) + f'amix=inputs={len(audio_labels)}:normalize=0:duration=first,atrim=duration={_time(duration_us)}[aout]')
    with tempfile.TemporaryDirectory(prefix='.render-', dir=destination.parent) as temporary:
        partial = Path(temporary) / 'render.mp4'
        # FFmpeg filter scripts avoid platform command-line length limits.
        script = Path(temporary) / 'filters.txt'
        script.write_text(';\n'.join(filters), encoding='utf-8')
        command += ['-filter_complex_script', str(script), '-map', '[vout]', '-map', '[aout]', '-t', _time(duration_us), '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-threads', '2', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-metadata:s:v:0', 'rotate=0', '-movflags', '+faststart', str(partial)]
        ctx.progress(None, 'Rendering original media')
        ctx.run(command, duration_us)
        ctx.progress(None, 'Validating export streams and decode')
        completed = probe(partial, ctx)
        rendered_video = next((s for s in completed['streams'] if s['codec_type'] == 'video'), None)
        rendered_audio = next((s for s in completed['streams'] if s['codec_type'] == 'audio'), None)
        measured_us = round(_seconds(completed.get('format', {}).get('duration')) * 1_000_000)
        tolerance_us = max(50000, round(1_000_000 * fps['den'] / fps['num']) + 25000)
        if not rendered_video or not rendered_audio:
            raise RuntimeError('Rendered export is missing expected video or audio stream')
        if (rendered_video['width'], rendered_video['height']) != (width, height):
            raise RuntimeError('Rendered export dimensions differ from project settings')
        if abs(measured_us - duration_us) > tolerance_us:
            raise RuntimeError(f'Rendered duration differs from timeline: expected {duration_us}, measured {measured_us} microseconds')
        ctx.run([binary('ffmpeg'), '-hide_banner', '-v', 'error', '-xerror', '-threads', '2', '-i', str(partial), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
        ctx.check_cancelled()
        if hasattr(ctx, 'publish_output'):
            ctx.publish_output(partial, destination)
        else:
            os.replace(partial, destination)
    return {'filename': destination.name, 'revision': project['revision'], 'duration_us': measured_us, 'expected_duration_us': duration_us, 'width': width, 'height': height, 'fps': fps, 'has_audio': True, 'verified': True, 'validation': 'ffprobe stream/duration checks and complete audio/video decode'}
