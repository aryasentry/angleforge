"""Decode-level source mapping tests, including arbitrary sub-frame boundaries."""
from pathlib import Path
import subprocess

import numpy as np
import pytest

from angleforge.exporter import render_project
from angleforge.media import binary, prepare_asset
from test_media import context


def make_clock(path, camera):
    # Each 100ms frame encodes camera and frame number in a solid RGB color.
    frames = np.empty((30, 32, 64, 3), dtype=np.uint8)
    for index in range(30):
        frames[index] = (180 if camera == 0 else 20, index * 7, 20 if camera == 0 else 180)
    subprocess.run([binary('ffmpeg'), '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '64x32', '-r', '10', '-i', '-', '-an', '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv444p', '-threads', '2', str(path)], input=frames.tobytes(), check=True)


def test_subframe_cut_source_mapping(tmp_path):
    assets, originals = {}, {}
    for camera in (0, 1):
        path = tmp_path / f'{camera}.mp4'
        make_clock(path, camera)
        ident = str(camera)
        assets[ident] = prepare_asset(path, tmp_path / f'derived{camera}', ident, path.name, context())
        originals[ident] = path
    starts, ends = [137000, 1083000, 557000], [841000, 1587000, 1099000]
    ids = ['0', '1', '0']
    segments, output = [], 0
    for index, (start, end, ident) in enumerate(zip(starts, ends, ids)):
        segments.append(dict(id=str(index), asset_id=ident, source_in_us=start, source_out_us=end, output_in_us=output, event_in_us=start, crop={'mode': 'fit', 'x': .5, 'y': .5}))
        output += end-start
    project = {'revision': 1, 'assets': assets, 'settings': {'width': 64, 'height': 32, 'fps': {'num': 30, 'den': 1}}, 'timeline': {'video': segments, 'audio': [], 'music': [], 'captions': []}}
    destination = tmp_path / 'subframe.mp4'
    render_project(project, originals.__getitem__, destination, context())
    decoded = subprocess.run([binary('ffmpeg'), '-v', 'error', '-i', str(destination), '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    frames = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 32, 64, 3)
    for index, frame in enumerate(frames):
        time_us = round(index * 1_000_000 / 30)
        if time_us >= output:
            break
        segment = next(s for s in segments if s['output_in_us'] <= time_us < s['output_in_us'] + s['source_out_us'] - s['source_in_us'])
        source_us = segment['source_in_us'] + time_us - segment['output_in_us']
        # Away from original-source frame boundaries, expect the held source frame.
        expected_frame = source_us // 100000
        observed = frame[16, 32].astype(int)
        expected_camera = 0 if segment['asset_id'] == '0' else 1
        assert (observed[0] > observed[2]) == (expected_camera == 0), f'Wrong camera at output {time_us}'
        allowed_frames = {max(0, (source_us + delta) // 100000) for delta in (-33334, 0, 33334)}
        assert any(abs(observed[1] - candidate * 7) <= 4 for candidate in allowed_frames), f'Source drift greater than one output frame at output {time_us}: source {source_us}, observed green {observed[1]}'
