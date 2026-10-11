# Editor visual system and browser verification

Reference: `../docs/editor-concept.png`, 1536 × 1024. Owner-approved implementation uses media library 230px, inspector 300px, flexible preview, full-width bottom timeline; compact flat panels. Original fixture artwork replaces the concept's synthetic test-chart artwork with real generated video. No static screenshot is used as UI.

## Design system

Charcoal background #111315, panel #191c20, border #30343a, off-white #e9ecef, muted #949ca7, lime #c8f07c. Flat 3–4px corner controls, no shadows except menus/dialogs. System sans typography at 13px; 10–12px metadata/controls; 17px brand; monospace output/source/event timecodes. All controls explicitly inherit UI typography. Lucide outline icons 13–20px, currentColor, standard stroke; lime selection outline and bottom tab accent. Blue/purple/teal sequential clip track colors; measured waveform in teal. Media frames have no tints or overlays except real caption content (captions currently unavailable) and error messages.

Reusable modules: App composition shell, token-aware button/field/section primitives, Library asset rows, Preview program monitor/angle rail/transport, Timeline ruler/video/master/music tracks, Inspector source/crop/audio/history, Director proposal and sync controls, durable Jobs view. State/API/pure mapping live separately.

## Intentional functional deviations from concept

- OS traffic lights, dummy lock/visibility controls, unsupported crop scale/edges omitted by owner approval.
- Required append, trim apply, output presets, Jobs tab, proposal review and selected-media disclosure extend the same visual system for actual workflows.
- Source-range inputs use decimal seconds, with precision-preserving integer conversion; display timecodes use mm:ss.mmm.
- Three real fixture sources replace illustrative chart media. Media image thumbnails and timeline filmstrips use the real generated representative thumbnail; synchronized rail uses seeked proxy videos.
- Captions/transcription have an explicit unavailable state because export does not support them.
- Mobile stacks media, preview, scrollable inspector and timeline; timeline remains horizontally scrollable.

## Fidelity ledger

| Comparison | Reference evidence | Implementation evidence / decision |
| --- | --- | --- |
| Palette | Charcoal flat panels, lime selected camera and export | Exact locked tokens; no gradient or tint |
| Workspace anatomy | Library left, preview center, inspector right, timeline bottom | Same anatomy; owner-approved 230/300px side widths |
| Typography | Compact sans chrome and monospace time display | 13px base, 10–12px metadata, fixed control font rules |
| Footage treatment | Dominant program monitor and angle strip | Actual proxy monitor and synchronized seeked angle videos |
| Timeline | Ruler, blue/purple/teal shot spans and green waveform | Position/duration from canonical microseconds; measured waveform |
| Selection | Lime camera outline and selected Inspector tab | Same outline and tab underline; selected timeline clip also lime |
| Controls | Split/ripple, playback, undo/redo/export | Same icon meanings; required truthful workflow controls added |
| Responsive | Desktop reference only | 1366×768 preserves columns; 390×844 stacks without page overflow |

Allowed visible copy includes all reference functional labels plus user-required editing, consent, progress, unavailable-state and job labels. No marketing copy, metrics or ornamental badges added. Concept-to-render comparison has been performed with `view_image`; native, laptop and mobile screenshots are under `../artifacts/editor-ui-*.jpg`. Workflow verification uses a dedicated Codex IAB tab. Viewport dimensions were read from the actual DOM and overrides reset after responsive checks.

Browser checks: real synthetic fixture preparation/import; timeline shot selection; proxy frame stepping; source/event/output mappings; playback through cuts; local portrait proposal reviewed then applied; undo restores output preset. Representative active playback samples: output 5.199s mapped B source 3.21049s (11.49ms deviation), output 10.801s mapped C source 6.836591s (35.591ms deviation, near one 30fps frame). Proxy readyState4 and paused=false observed. Angle rail seeks are throttled to 120ms to reduce decode work. Source cut AbortError from superseded play requests is ignored; actual media errors remain visible. Dropped-frame counter is unavailable in IAB's exposed media API; no drop-rate claim is made.

## Compact desktop height refinement

At desktop width ≥801px and height ≤800px, the initial timeline is 220px (still resizable); angle pictures are 75px, with modestly reduced transport/rail spacing. Native 1536×1024 remains unchanged: 260px timeline, 111px angles, 753.77×424px program image.

The original 1280×720 program monitor measured approximately 214×120px. The refined monitor measures 398.22×224px, with 75px angle pictures and no horizontal page overflow. Owner IAB verification captured `../artifacts/editor-final.jpg` with Camera B selected at 6s and all videos at readyState 4. A separate read-only Playwright Chromium check measured 1280×720 and 1536×1024 and captured `../artifacts/editor-ui-compact.jpg` without changing project state. `view_image` confirmed the compact capture, the owner final capture, and the reference concept; the program monitor now has clear visual priority over angle thumbnails.
