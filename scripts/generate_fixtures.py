#!/usr/bin/env python3
"""Generate explicitly labeled, deterministic multicamera test footage locally."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from angleforge.media import binary


def event_audio(rate=16000, duration=12):
    rng = np.random.default_rng(74291)
    t = np.arange(rate * duration) / rate
    result = np.zeros_like(t)
    # Nonperiodic bandlimited noise bursts and unique chirps throughout the event.
    for index, start in enumerate([.15, .62, 1.37, 2.08, 2.91, 3.53, 4.11, 4.83, 5.76, 6.14, 7.01, 7.89, 8.31, 9.17, 10.06, 10.91, 11.48]):
        length = rng.uniform(.16, .42)
        mask = (t >= start) & (t < start + length)
        local = t[mask] - start
        frequency = rng.uniform(240, 1300)
        envelope = np.sin(np.pi * local / length) ** 2
        chirp = np.sin(2 * np.pi * (frequency * local + rng.uniform(80, 800) * local ** 2))
        noise = rng.normal(0, .2, len(local))
        result[mask] += envelope * (.35 * chirp + noise)
    result += rng.normal(0, .005, len(t))
    return np.clip(result, -.9, .9)


def generate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rate = 16000
    signal = event_audio(rate)
    font = next((str(p) for p in [Path('/System/Library/Fonts/Monaco.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('C:/Windows/Fonts/consola.ttf')] if p.exists()), None)
    # FFmpeg filter-option colons need escaping on Windows drive paths.
    escaped_font = font.replace('\\', '/').replace(':', r'\:') if font else None
    font_arg = f"fontfile='{escaped_font}':" if escaped_font else ''
    files = []
    for camera, offset, color, dimensions in [('A', 0, '0x254b65', '640x360'), ('B', 2, '0x665037', '640x360'), ('C', 4, '0x51466b', '360x640')]:
        wav = output / f'.camera_{camera}.wav'
        samples = (signal[offset * rate:] * 32767).astype('<i2')
        with wave.open(str(wav), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(samples.tobytes())
        target = output / f'synthetic_camera_{camera}.mp4'
        duration = 12 - offset
        text = f"SYNTHETIC CAMERA {camera}"
        vf = f"drawbox=x=20:y=20:w=iw-40:h=84:color=black@0.6:t=fill,drawtext={font_arg}text='{text}':x=32:y=35:fontsize=22:fontcolor=white,drawtext={font_arg}text='event %{{eif\\:t+{offset}\\:d}} s / source %{{eif\\:t\\:d}} s':x=32:y=72:fontsize=16:fontcolor=white,drawbox=x=40+mod(t*43\\,180):y=ih/2:w=55:h=55:color=0xc8f07c:t=fill"
        args = [binary('ffmpeg'), '-v', 'error', '-y', '-threads', '2', '-f', 'lavfi', '-i', f'color=c={color}:s={dimensions}:r=30:d={duration}', '-i', str(wav), '-vf', vf, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-t', str(duration), '-movflags', '+faststart', str(target)]
        try:
            subprocess.run(args, check=True)
        finally:
            wav.unlink(missing_ok=True)
        files.append({'camera': camera, 'file': target.name, 'event_offset_us': offset * 1_000_000, 'duration_us': duration * 1_000_000})
    manifest = {'synthetic': True, 'event_duration_us': 12_000_000, 'seed': 74291, 'files': files, 'expected_sync_error_us': 50000}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('data/fixtures'))
    args = parser.parse_args()
    print(json.dumps(generate(args.output), indent=2))
