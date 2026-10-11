# External director and offline commands

Manual editing, measured synchronization, local analysis, undo/redo and export do not need an API key. **Offline commands are a literal parser, not AI.** The external director is a real backend adapter: it submits a bounded request and returns an editable proposal against the current project revision. It never commits the proposal or launches tools. The same deterministic timeline validator used for manual edits dry-runs every proposal. Applying it later requires the same revision; intervening edits make it stale.

## Configuration

Set these in the backend environment (or the ignored local `.env` loaded by the launcher), never in frontend configuration or a project document:

```dotenv
ANGLEFORGE_AI_PROVIDER=openrouter
ANGLEFORGE_AI_MODEL=your-current-catalog-model-id
OPENROUTER_API_KEY=replace-locally
# Zen uses OPENCODE_API_KEY instead:
# ANGLEFORGE_AI_PROVIDER=zen
# ANGLEFORGE_AI_MODEL=muse-spark-1.3
# OPENCODE_API_KEY=replace-locally
ANGLEFORGE_AI_MAX_TOKENS=1600
ANGLEFORGE_AI_RETRIES=1
ANGLEFORGE_AI_TIMEOUT_SECONDS=45
ANGLEFORGE_AI_TOTAL_OUTPUT_TOKENS=4800
```

`/api/provider` reports configuration presence and budgets without revealing keys. Model IDs can be chosen from the public catalog via `/api/provider/models?provider=openrouter` or `provider=zen`. Catalog requests send no Authorization header, perform no inference and are cached for five minutes. Keys are sent only to fixed HTTPS inference endpoints; redirects are disabled. Provider errors withhold response bodies. Responses containing the runtime key are rejected before parsing or displaying them.

## Model discovery and capability evidence

OpenRouter is the default provider and uses `https://openrouter.ai/api/v1/chat/completions`. Its public model catalog advertises input modalities and supported parameters. For models advertising `structured_outputs`, requests include a strict JSON Schema and `provider.require_parameters=true` to request compatible routing. Other text models receive an explicit JSON-format instruction; their output remains untrusted and must parse and pass the identical deterministic validation. This adapter does not execute tool calls.

Zen supports documented `responses` and `chat/completions` protocols. The official endpoint table inspected on **2026-10-11** lists `muse-spark-1.3`, `muse-spark-1.2`, `muse-spark-1.3-contributor-free` and the documented GPT/Grok IDs at `https://opencode.ai/zen/v1/responses`; documented compatible models such as `big-pickle`, GLM and Kimi use `https://opencode.ai/zen/v1/chat/completions`. Responses requests use `input`, `max_output_tokens`, and `store:false`; returned output text is parsed as JSON. Zen models requiring Anthropic Messages, Google or System One protocols are displayed but unavailable for this adapter.

The live Zen catalog returned IDs but **no modality, tool-call or strict-schema metadata**. Accordingly, those capabilities are shown as unverified; the director uses text-only JSON proposals without claiming strict provider enforcement. Unknown future IDs have no inferred route and fail explicitly. Alpha or preview models are usable only when their actual catalog ID and supported protocol are verified; no fictional alpha ID is supplied. No alpha-named ID appeared in the catalog inspected on 2026-10-11. Availability and prices may change; refresh the catalog. Contributor/free naming is not a promise about billing or privacy.

The capability and protocol rules come from these official sources:

- [Zen endpoint table and public catalog documentation](https://opencode.ai/docs/zen/)
- [OpenRouter model properties](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
- [OpenRouter structured output support and routing](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling)
- [OpenRouter video input formats and model compatibility](https://openrouter.ai/docs/guides/overview/multimodal/videos)

## Consent and budgets

Every external plan requires `consent.metadata=true` and one to twenty explicitly selected unique asset IDs. Empty selections never fall back to all assets. The UI discloses provider/model and data before requesting a proposal: your instruction, selected asset names/properties, their synchronization offsets, output settings and selected-source timeline segments. Other asset details and all caption text are omitted. Existing local analysis is included only for selected assets when its fingerprint/version still match: at most twelve timestamped luma/contrast/change measurements and twenty-four coarse scene candidates per asset, plus aggregate numeric audio RMS/clipping/silence levels. Paths, image URLs, transcripts, free-text analysis and non-finite measurements are omitted. Numeric audio levels do not transfer audio samples. Original videos, audio and transcripts are not sent. Metadata-only planning cannot identify people, action or speech from footage, and semantic claims must not be inferred from filenames.

**Optional sampled-frame planning** additionally requires `consent.frames=true`, an OpenRouter model whose current catalog advertises `image` input modality, and one to three explicitly selected ready video assets. The coordinator prepares/cache-reads local analysis and injects internally trusted JPEG samples; the browser cannot supply arbitrary frame paths or URLs. At most four frames per asset and twelve frames total leave the machine, with asset IDs and source timestamps. Each encoded JPEG is bounded to 750,000 characters. The director rejects out-of-selection samples, out-of-range timestamps, invalid JPEG data URLs, over-budget frames, missing consent, and Zen image requests because Zen's image capability is unverified. The adapter checks model image capability before inference. No full video or audio is transferred.

The director is instructed to tie sparse visual observations to source timestamps and distinguish observations, uncertainty and guesses in its summary. Sampled frames may miss fast action; they are not continuous-video evidence and the model must not claim every frame was watched. The returned proposal disclosure records only sample count/timestamps, never image data. Transcription remains unavailable. AI caption operations are disabled while export lacks caption support.

The selected model must exist in the current catalog and support the adapter's text protocol. Requests cap instruction length at 6,000 characters, serialized project metadata at 48,000 characters, metadata asset selection at 20 IDs (three for frame planning) and editing proposals at 100 operations. Output tokens default to 1,200 per request with a configurable 1,600 ceiling (hard maximum 4,096). The maximum number of attempts is the smaller of configured retries plus one (hard maximum three attempts) and total output token budget divided by per-attempt token limit. This budget bounds requested output tokens; input/reasoning accounting depends on the provider and is not a guaranteed dollar spending cap.

Only HTTP 429/503 responses can trigger bounded retry. Connection errors and timeouts are not resubmitted automatically because a submission may already be billed. Each attempt has a configurable 45-second default timeout (hard maximum 90 seconds). Cooperative cancellation checks run while awaiting the HTTP call and cancel the local request; remote billing or inference may already have started. Returned numeric token usage and cost, when present, are shown as provider data. No cost estimate is fabricated and no live inference was made during implementation or tests. Runtime inference has billing independent of the coding tools.

## Offline grammar

Supported examples (names may be full filenames, IDs, camera letters or one/two/three):

- `use camera B from 2 to 5 seconds and keep camera A audio`
- `use camera two between 12 and 18 seconds`
- `keep the audio from camera one throughout`
- `make a 5-second highlight from these clips` — explicitly a literal first-five-second duration cut, **not semantic highlight selection**. If the timeline is empty, it uses the first ready video; it refuses missing coverage.
- `hold first shot 1 second longer` — switches the following output interval to the first shot's camera while preserving event/source mapping; it refuses unavailable footage.
- `make a vertical version` — 1080 × 1920 output; adjust crop using manual controls.

The parser is anchored to the complete command, with no shell evaluation or privileged operations. Other natural-language requests require the configured external provider. Use Undo to undo a prior AI or manual transaction.

## Measured synchronization and manual correction

The sync worker reads video-origin-aligned audio WAVs, resamples to 2 kHz and computes normalized cross-correlation using each lag's actual overlap mean/energy. At most eight videos and twenty minutes per audio file are accepted. WAV headers are memory-mapped so this duration check happens before materializing audio samples. Pairwise matches must have correlation at least 0.50 and beat alternative peaks more than 150 ms away by at least 0.10. It builds an overlap graph, propagates offsets from the chosen reference, and refuses disconnected, quiet, repetitive or conflicting measurements. A cycle inconsistency over 50 ms is refused. The 2 kHz grid provides 500 µs resolution, not a guarantee of recording accuracy.

Separate head/tail window measurements on overlaps of at least 30 seconds detect residual changes over 25 ms and warn of possible clock drift. Weak global matches also receive independent window checks where possible. Time stretching is not implemented; inspect manual anchors. These checks can miss drift in noisy or repetitive recordings.

`set_sync` applies a reviewed proposal. Automatic result `diagnostics` are explanatory and must be removed from the operation payload. Manual anchors have `{asset_id,source_us,event_us}`; the corresponding offset must equal `event_us-source_us`. Missing audio or uncertain evidence requires manual offsets or matching anchors. Changing offsets preserves segment source ranges and recalculates event mapping. No footage is invented outside the available source range.

Tests cover known offsets, an indirect graph connection, periodic ambiguity, disconnected/silent audio, cancellation, drift diagnostics, metadata/frame consent and selected-data isolation, model image capability gating, sparse-frame timestamps/budgets, stale revision, prompt injection, unsupported operations, source coverage, absent keys, catalog capability mapping, Responses/chat payloads, budgets, malformed responses and credential-echo refusal. Real AAC fixture audio produced A=0 s, B=2 s, C=4 s with 0 ms measured offset error in this run (algorithm grid 0.5 ms); confidence B=0.99866, C=0.99857. This is synthetic media evidence, not proof for every recording. Text protocols and OpenRouter sampled-frame requests are tested with mocked HTTP responses; live inference and account billing remain unverified.
