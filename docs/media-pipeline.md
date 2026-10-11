# Media processing and verification

AngleForge uses native FFmpeg processes with argument arrays, a two-worker pool, and capped encoder/filter threads. No media is sent to an external service during import, synchronization, local analysis, preview, or export.

## Timestamp and channel mapping

The canonical time unit is an integer microsecond. Source zero is the original video stream's first presentation timestamp; the private metadata records this as `normalization.source_origin_us`, and the public asset declares it as `proxy.source_origin_us`. Audio is trimmed or padded to that same origin. Rotation is applied during decoding. Source timing remains time based when the original is variable frame rate.

Video proxies are H.264, 30 fps, capped at 960 pixels in either dimension without upscaling. Thumbnail JPEGs, waveform JSON, and private original stream metadata accompany them. `audio.wav` is mono PCM16 at 16 kHz for synchronization and measurements. Browser master/music playback uses `monitor.m4a`, AAC stereo at 48 kHz, preserving the original stereo channels; mono sources are duplicated explicitly to preserve gain. The original is retained unchanged.

Export reads originals and the canonical project. Video source samples use the declared output frame rate. Segment frame counts come from the **global output boundaries**, so arbitrary subframe cuts do not accumulate rounding drift. Each cut occurs on the first output frame at or after its microsecond boundary. Decoded acceptance tests check camera identity and source mapping within one declared output frame, including low-FPS sources and nonintegral cut boundaries. Fit/fill transforms and crop position are applied to original media. Hard cuts are supported; transitions are outside this release.

Master and music audio use their explicit source intervals, output positions, gain, mute, and fade settings. Delays are quantized to the 48 kHz sample clock. Decoded tests compare the exported PCM waveform with independently assembled source audio, including silence between segments. AAC is lossy; correlation exceeds 0.95 and measured gain falls within the stated test tolerances (12–15%).

Exports are H.264/AAC MP4. Before publication, ffprobe checks streams, dimensions, and duration, followed by a complete audio/video decode with errors treated as failures. The final file appears through an atomic rename. Worker finalization excludes cancellation during publication and job commit; failures after publication restore an earlier destination. Canceling an active render terminates its process and removes its temporary output. Captions fail clearly because portable caption rendering is unavailable.

## Analysis and recovery

Local analysis samples luma and scene-change scores at one frame per second, measures audio RMS/clipping/silence, and extracts six contact JPEGs. Contact frame `image_source_us` is the actual requested source timestamp; `source_us` identifies the separate per-second measurement. Fast action may be missed. Luma measurements cover at most the first ten minutes, with an explicit warning for longer clips. Contact images span the whole source. Transcription is explicitly unavailable without a speech model.

Analysis schema version 2 is cached by asset fingerprint. A cancel-aware per-asset lock serializes local analysis and AI frame preparation. JPEGs and caches are written to unique temporary files and atomically replaced. Concurrent callers reuse the same completed cache.

SQLite persists job status/results. Pending/running jobs are marked failed after restart. Private `.prepare-*` and `.render-*` directories are reclaimed without deleting originals or finished exports. The pending queue is bounded at 64 jobs. Cancellation uses POSIX process groups; the Windows path uses `taskkill /PID … /T /F` to terminate the child tree.

## Tested environment

The verified host is macOS on Apple Silicon. The locally available binaries were **FFmpeg 6.0** and **ffprobe 4.4-tessus**; they are private verification tools, excluded from source distribution. The resolver checks `ANGLEFORGE_FFMPEG` / `ANGLEFORGE_FFPROBE`, then PATH, private `.tools/bin`, and `/opt/homebrew/bin`. Preflight reports the actual versions and requires the libx264 and AAC encoders.

Windows subprocess/path branches were reviewed, but Windows installation, playback, and rendering were **not executed**. They must not be described as runtime verified.

Reproduce the labeled 12-second, nonperiodic-audio event with:

```sh
.venv/bin/python scripts/generate_fixtures.py --output data/fixtures
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_jobs.py backend/tests/test_media.py backend/tests/test_export.py backend/tests/test_render_mapping.py -q
```

Camera A covers event 0–12 seconds, B covers 2–12 seconds, and portrait camera C covers 4–12 seconds. The manifest records the ground-truth offsets and seed; all footage is visibly labeled synthetic.
