# AngleForge implementation contract

The user supplied a detailed product and engineering brief and authorized reasonable implementation decisions and parallel coding. This document is the shared implementation contract; the integration owner controls changes to it.

## Architecture and design

Python 3.11–3.13, FastAPI, SQLite, native FFmpeg/ffprobe and a React/TypeScript/Vite browser editor. Vite is chosen over Next.js because this is a local editor with no server-rendering requirement; a production build is served by FastAPI on the same origin. No Docker, accounts, GPU or cloud calls are required for editing. The backend binds to 127.0.0.1:8766 (8765 was occupied by another local application). Local assets and state live in data/ (configurable ANGLEFORGE_DATA_DIR). UI: charcoal #111315, panels #191c20, borders #30343a, off-white text #e9ecef, muted #949ca7, lime #c8f07c accent. 13px UI, monospace timecodes; flat resizable panels, generous central preview, visible angle rail, bottom timeline. Synthetic media must be visibly labeled.

## Canonical schema v1

All times are integer microseconds (`timebase: 1000000`). End boundaries are exclusive. Rational FPS is `{num:30,den:1}`. Playback uses time, never source frame index. Assets are keyed by stable UUID. An asset's original bytes are copied into its private directory; paths are never part of the public project. FFmpeg normalizes the video start timestamp to proxy zero; `proxy.source_origin_us` declares the original first-video-frame timestamp and source times are measured relative to that origin.

Project shape:
```
{schema_version:1, id, name, revision:0, timebase:1000000,
 settings:{width:1920,height:1080,fps:{num:30,den:1}},
 assets: {[id]: {id,name,kind:'video'|'audio',status:'processing'|'ready'|'failed',
 duration_us,width,height,fps:{num,den},has_audio,rotation:0,
 fingerprint,proxy:{source_origin_us:0,timebase:1000000},
 waveform: number[],error:null}},
 sync:{reference_asset_id:null,offsets:{[id]:integer},confidence:{[id]:number},
       anchors:[],warnings:[],edges:[]},
 timeline:{video:[],audio:[],captions:[],master_audio_asset_id:null,music:[]},
 analysis:[], history:[],can_undo:false,can_redo:false}
```
Video segment: `{id,asset_id,source_in_us,source_out_us,output_in_us,event_in_us,crop:{mode:'fit'|'fill',x:0.5,y:0.5}}`. Duration = source_out_us-source_in_us. Output video is contiguous, sorted, without overlap. `event_in_us` is the synchronized event time of segment start. source = event - sync.offsets[asset_id]. Removing/reordering output never alters sync offsets. Existing segment source mappings remain stable when offsets are changed; their event mapping is recalculated. Missing source coverage is rejected explicitly.

Audio segment: `{id,asset_id,source_in_us,source_out_us,output_in_us,gain:1,muted:false,fade_in_us:0,fade_out_us:0}`. Master audio is rebuilt deterministically against each video segment's event interval on structural edits; partial source coverage leaves explicit silence and a warning. User gain/mute/fades persist. Music uses explicit output positions (does not ripple automatically). Captions `{id,start_us,end_us,text}` are optional manual text; no transcription is fabricated.

## Editing API

All JSON responses, failures `{detail:string}`. Client fetches GET /api/session -> `{token}` then sends `X-AngleForge-Token` for mutations; browser Origin must match loopback app origins. No unrestricted media filesystem endpoint. Runtime keys stay only in environment/.env. GET /api/health includes preflight. API GETs include project list, project, jobs, provider status.

- GET /api/projects -> `{projects:[{id,name,revision,updated_at}]}`
- POST /api/projects `{name}` -> Project
- GET /api/projects/{id} -> Project
- POST /api/projects/{id}/import multipart `files` -> `{jobs:[Job]}`; import completion adds ready asset to project atomically, increments revision.
- POST /api/projects/{id}/transactions `{base_revision,label,actor:'manual'|'ai',operations:[Operation],proposal_job_id?:UUID}` -> Project. AI edits require a completed matching proposal job; the coordinator verifies exact operations/project/revision and stores provider/model/usage provenance in history and analysis.
- POST /api/projects/{id}/undo and /redo `{base_revision}` -> Project. Revision monotonically increases even on undo.
- POST /api/projects/{id}/save -> `{saved:true,revision}`
- GET /api/projects/{id}/document -> project JSON attachment (no paths/secrets).
- POST /api/projects/{id}/sync `{base_revision}` -> `{job:Job}`; results stored in job.result as proposals; apply through `set_sync` transaction.
- GET /api/jobs -> `{jobs:[Job]}`; GET /api/jobs/{id}; POST /api/jobs/{id}/cancel -> Job.
- POST /api/projects/{id}/export `{base_revision,name:'my-edit.mp4'}` -> `{job:Job}`. Only basename, output under data/exports; job result has download_url and verified details. Snapshot revision exported.
- GET /api/media/{asset_id}/{variant} where variant `proxy|thumbnail|original|audio|monitor|analysis-0..5` with byte range support; no paths accepted. Audio is 16 kHz mono WAV for measured analysis/sync; monitor is 48 kHz stereo AAC for browser playback.
- POST /api/projects/{id}/analyze `{asset_id}` -> `{job:Job}` local deterministic measurements/contact frames only.
- GET /api/provider -> safe provider config/status. GET /api/provider/models?provider=openrouter|zen -> `{models:[...]}` public capability catalog (no inference).
- POST /api/projects/{id}/ai/plan `{base_revision,prompt,provider,model,consent:{metadata:true,frames:false},asset_ids:[selected IDs],max_tokens:1200}` -> `{job:Job}`. Requires configured key and explicit consent/nonempty asset selection; job.result `{base_revision,label,operations,summary,usage,provider,model,proposal_job_id}`. OpenRouter frames additionally require separate consent, verified image capability and 1–3 selected clips (max four JPEGs each). The backend creates frame payloads from scoped cache files; client-supplied image paths/URLs are discarded. Never automatically commit. Applying is the normal transaction route with actor ai; stale plans get 409.
- POST /api/projects/{id}/command `{base_revision,prompt}` -> same plan shape for a small explicitly labeled offline command grammar (not LLM AI).

Job `{id,kind,status:'queued'|'running'|'succeeded'|'failed'|'canceled',progress:0..1|null,message,result:null|object,error:null|string,created_at,updated_at}`. Poll 1 sec for durability/simplicity; SSE is deferred and documented. Worker concurrency 2, process threads capped, queued/running recovered as failed after restart, cancellation terminates subprocesses and cleanup removes partial output.

## Operation discriminated union

Operations are strictly validated, unknown keys rejected, max 100 ops. `insert_segment {asset_id,source_in_us,source_out_us,index?}`; `split_segment {segment_id,at_us}` (at_us is OUTPUT time); `trim_segment {segment_id,source_in_us,source_out_us}`; `remove_segment {segment_id,ripple:true}`; `reorder_segments {segment_ids:[all ids exactly once]}`; `switch_angle {asset_id,start_us,end_us}` (output interval, splits boundaries and maps via event time); `set_audio_source {asset_id|null}`; `set_audio_gain {gain:0..4,muted?:bool,fade_in_us?:int,fade_out_us?:int}`; `set_crop {segment_id,mode:'fit'|'fill',x:0..1,y:0..1}`; `set_settings {width,height,fps:{num,den}}`; `rename_project {name}`; `set_sync {reference_asset_id,offsets,confidence?,anchors?,warnings?,edges?}`; `add_music {asset_id,output_in_us,source_in_us,source_out_us,gain}`; `remove_music {id}`; `add_caption {start_us,end_us,text}`; `remove_caption {id}`. No source deletion, export, arbitrary file/shell/network tool exists in editing schema.

## Python integration interfaces

Media agent owns `media.py`, `jobs.py`, `exporter.py`, media/export tests, `scripts/generate_fixtures.py`.
`media.preflight() -> dict`; `media.prepare_asset(original:Path,derived:Path,asset_id:str,name:str,ctx) -> asset dict`; filenames proxy.mp4, thumbnail.jpg, audio.wav, waveform.json, metadata.json under derived directory; `media.analyze_asset(original,derived,asset,ctx)->dict`.
`jobs.JobManager(data_dir:Path,max_workers=2)` with `submit(kind,fn)->Job`, `get(id)->Job`, `list()->list[Job]`, `cancel(id)->Job`, `shutdown()`. fn receives JobContext; `ctx.run(args:list[str],duration_us:int|None=None)->str` returns stdout, `ctx.progress(value,message)`, `ctx.check_cancelled()`. fn returns result dict.
`exporter.render_project(project:dict,resolve_original:Callable[[str],Path],destination:Path,ctx)->dict`; exact canonical project drives original media render, transforms and audio. Atomic destination publication only after ffprobe/decode validation. Captions may be rejected with clear unsupported error if font portability not dependable.

Sync/AI agent owns `sync.py`, `providers.py`, `director.py`, their tests and provider docs.
`sync.propose_sync(assets:dict,audio_paths:dict[str,Path],reference_id:str|None=None,ctx=None)->dict` returns set_sync fields plus diagnostics. Must refuse ambiguous/disconnected offsets.
`providers.provider_status()->dict`; async `providers.list_models(provider)->dict`; async `director.plan_edit(project,request:dict,ctx=None)->dict`; `director.parse_local_command(project,prompt)->dict`. Safe plan only, no persistence. Need Responses endpoint support for Zen Muse Spark. `ctx` cancellation checks. Use public catalogs/capability evidence, no invented capabilities.

Integration owner owns `timeline.py`, `store.py`, `app.py`, schemas, root manifests/launchers/docs/integration tests. UI agent owns frontend/** only. Changes to these interfaces must be messaged to owner before implementing incompatible changes.

## Verification plan

Unit tests for integer mapping, half-open boundaries, sync evidence and rejection, stale transactions, exact undo content, provider payload validation and failures. Reproducible labeled fixtures: camera A 0–12s, B 2–12s, C 4–12s, shared deterministic nonperiodic audio and visible source/event time; expected error <= 50 ms. Real FFmpeg ingestion, portrait/landscape and decode validation, cancellation; browser flow import → append → split → switch → local command/LLM mocked adapter → undo/redo → reopen → export. External live inference is unverified until separately authorized; no credentials needed for the test suite. macOS host verified; Windows instructions are provided and code paths reviewed, Windows runtime not claimed.
