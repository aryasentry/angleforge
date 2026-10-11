# Verification — 2026-10-11

## Executed

- macOS Apple Silicon; Python 3.13.13; Node 24.19.0. Native FFmpeg 6.0 / ffprobe 4.4-tessus were available privately and are excluded from source distribution.
- Full Python suite: **93 passed in 33.22 seconds**. One Starlette test-client deprecation warning about httpx; no failed tests. Provider network tests use mock transports, never live paid inference.
- Frontend: **5 tests passed**; TypeScript check and Vite production build passed. Production JS was about 274 kB / 85 kB gzip.
- Real FFmpeg tests include VFR/rotation, delayed audio origin, 48 kHz stereo monitoring, known-offset/ambiguous/disconnected synchronization, subframe cut/source mapping, original-media portrait/landscape export, audio cuts/gain/fades, cancellation/process cleanup, publication rollback and concurrent analysis.
- Synthetic audio alignment: A=0 s, B=2 s, C=4 s; measured maximum error 0 ms on a 0.5 ms correlation grid. Declared acceptance tolerance 50 ms. This is not evidence for all real-world recordings.
- Decode-level cuts map to the expected source within one output frame; global output-frame allocation avoids per-cut rounding accumulation. Audio cut/gain/fade tests exceed 0.95 waveform correlation and stay within 12% AAC amplitude tolerance.

## Actual browser workflow

Used Codex in-app browser with Playwright locators and native slider controls, not a mocked frontend. Imported all three generated MP4 files via the file chooser, waited for ready proxies, appended camera A, requested measured synchronization, reviewed and applied the proposal, selected/searched the timeline, split at four seconds, and reviewed/applied the literal command `use camera B from 4 to 8 seconds and keep camera A audio`. Undo restored the prior timeline and redo restored the camera change. Camera C was applied from eight to twelve seconds. Saved, restarted the backend, reloaded the browser and verified the same three segments survived. Renamed through the inline form and rendered the final MP4 through Export.

The owner project is `4af7c71d-272e-4242-a2f5-05d5bb4e5d8c`, revision 14. Its observed reference was camera B, so event offsets are A=-2/B=0/C=2 seconds; source and output mappings remain equivalent to the fixture ground truth. New imports now choose the first submitted video as reference deterministically.

The local command test is **not a live LLM test**. OpenRouter/Zen Chat Completions/Responses, image capability gating, scoped consent, stale proposals, untrusted instructions, schema rejection, token limits and in-flight cancellation were tested through backend mock HTTP transports. No runtime API keys were configured or paid calls made.

Browser playback crossed camera cuts and reached the end. Representative measured source-time deviations were 11.49 ms and 35.59 ms; decoders reported readyState 4. Dropped-frame counters were unavailable through the inspection surface, so no drop-rate claim is made. Native desktop 1536×1024, laptop 1366×768, mobile 390×844 and the actual owner viewport 1280×720 were inspected. The mobile page had no horizontal overflow. The final compact layout at 1280×720 has a 398.22×224 program monitor, 75px angle pictures, a resizable 220px timeline and no horizontal overflow; all four videos reported readyState 4. The frontend's five tests and production build passed again after this refinement. See `frontend/DESIGN-VERIFICATION.md` for the eight-point concept/layout comparison and intentional functional differences.

## Verified sample

`artifacts/angleforge-verified-multicam.mp4`: **12.000000 seconds**, **1920×1080**, **30/1 fps**, H.264 video, AAC **48 kHz stereo**, **374,894 bytes**. The backend verified streams, dimensions, duration and full decode before atomic publication; an independent ffprobe and full FFmpeg decode also passed after copying the sample. `artifacts/sample-validation.json` records the mappings and probe facts; `artifacts/sample-project.json` records the editable document. Regenerate source clips using `scripts/generate_fixtures.py` or Load synthetic demo. Sample JSON references local asset IDs; it is not a standalone media bundle.

Screenshots: `artifacts/editor-final.jpg` (owner's running editor), `editor-ui-native.jpg`, `editor-ui-laptop.jpg`, `editor-ui-mobile.jpg`. The visual reference is `docs/editor-concept.png`, generated with the built-in image tool; it was inspected alongside real browser captures. Functional deviations replace concept-only controls and artwork with implemented operations and real labeled footage.

## Limits and unverified behavior

Windows setup/process paths are implemented and documented but were not executed. No live external model response, provider billing or semantic editing quality was validated. No consented real event clips were provided; media acceptance uses labeled synthetic fixtures. Captions/transcription, automatic person tracking, drift correction, transitions, collaborative/cloud workflows, desktop packaging and SSE are deferred. Model capability discovery and HTTP adapter tests do not establish live account access. See README and provider/media docs for exact supported operations and resource limits.
