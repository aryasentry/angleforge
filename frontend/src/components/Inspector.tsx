import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Music2,
  Search,
  Volume2,
} from "lucide-react";
import { api } from "../api";
import { duration, microseconds, seconds, timecode } from "../timeline";
import type { Editor } from "../useEditor";
import type { Job, Segment } from "../types";
import { Button, Numeric, Section } from "./Primitives";
export function Inspector({
  editor,
  selected,
  selectedAsset,
}: {
  editor: Editor;
  selected: Segment | undefined;
  selectedAsset: string | null;
}) {
  const p = editor.project!,
    asset = p.assets[selected?.asset_id ?? selectedAsset ?? ""],
    [sourceIn, setSourceIn] = useState(0),
    [sourceOut, setSourceOut] = useState(0),
    [gain, setGain] = useState(1),
    [fadeIn, setFadeIn] = useState(0),
    [fadeOut, setFadeOut] = useState(0),
    [crop, setCrop] = useState({
      mode: "fit" as "fit" | "fill",
      x: 0.5,
      y: 0.5,
    });
  useEffect(() => {
    setSourceIn(seconds(selected?.source_in_us ?? 0));
    setSourceOut(seconds(selected?.source_out_us ?? asset?.duration_us ?? 0));
    setCrop(selected?.crop ?? { mode: "fit", x: 0.5, y: 0.5 });
  }, [selected?.id, p.revision, asset?.id]);
  useEffect(() => {
    const s = p.timeline.audio_settings ?? p.timeline.audio[0];
    setGain(s?.gain ?? 1);
    setFadeIn(seconds(s?.fade_in_us ?? 0));
    setFadeOut(seconds(s?.fade_out_us ?? 0));
  }, [p.revision]);
  const move = (delta: number) => {
    if (!selected) return;
    const ids = p.timeline.video.map((s) => s.id),
      i = ids.indexOf(selected.id),
      to = i + delta;
    if (to < 0 || to >= ids.length) return;
    [ids[i], ids[to]] = [ids[to], ids[i]];
    void editor.transact("Reorder shots", [
      { type: "reorder_segments", segment_ids: ids },
    ]);
  };
  return (
    <div className="inspector-content">
      <Section title="Source">
        <div className="source-name">
          {asset?.name ?? "Select a clip"}
          <span>{asset?.kind ?? ""}</span>
        </div>
        {selected ? (
          <>
            <Numeric
              label="Source in (sec)"
              value={sourceIn}
              min={0}
              max={sourceOut}
              onChange={setSourceIn}
            />
            <Numeric
              label="Source out (sec)"
              value={sourceOut}
              min={sourceIn}
              max={seconds(asset.duration_us)}
              onChange={setSourceOut}
            />
            <Button
              className="wide"
              disabled={editor.busy || sourceOut <= sourceIn}
              onClick={() =>
                void editor.transact("Trim selected shot", [
                  {
                    type: "trim_segment",
                    segment_id: selected.id,
                    source_in_us: microseconds(sourceIn),
                    source_out_us: microseconds(sourceOut),
                  },
                ])
              }
            >
              <Check size={14} /> Apply trim
            </Button>
            <div className="field">
              <span>Output range</span>
              <span className="mono">
                {timecode(selected.output_in_us)}–
                {timecode(selected.output_in_us + duration(selected))}
              </span>
            </div>
            <div className="button-row">
              <Button
                onClick={() => move(-1)}
                disabled={
                  p.timeline.video[0]?.id === selected.id || editor.busy
                }
              >
                <ArrowLeft size={13} /> Earlier
              </Button>
              <Button
                onClick={() => move(1)}
                disabled={
                  p.timeline.video.at(-1)?.id === selected.id || editor.busy
                }
              >
                Later <ArrowRight size={13} />
              </Button>
            </div>
          </>
        ) : (
          <p className="muted">
            Select a timeline shot to trim and transform it.
          </p>
        )}
        {asset && (
          <Button
            className="wide"
            disabled={editor.busy}
            onClick={() =>
              void editor.run(() =>
                api<{ job: Job }>(`/projects/${p.id}/analyze`, {
                  asset_id: asset.id,
                }),
              )
            }
          >
            <Search size={14} /> Measure this clip
          </Button>
        )}
      </Section>
      <Section title="Transform">
        <label className="field">
          <span>Output</span>
          <select
            aria-label="Output preset"
            value={`${p.settings.width}x${p.settings.height}`}
            onChange={(e) => {
              const [width, height] = e.target.value.split("x").map(Number);
              void editor.transact("Change output format", [
                { type: "set_settings", width, height, fps: p.settings.fps },
              ]);
            }}
          >
            <option value="1920x1080">Landscape · 1920 × 1080</option>
            <option value="1080x1920">Portrait · 1080 × 1920</option>
            {!["1920x1080", "1080x1920"].includes(
              `${p.settings.width}x${p.settings.height}`,
            ) && (
              <option value={`${p.settings.width}x${p.settings.height}`}>
                {p.settings.width} × {p.settings.height}
              </option>
            )}
          </select>
        </label>
        <label className="field">
          <span>Fit</span>
          <select
            aria-label="Crop fit mode"
            disabled={!selected}
            value={crop.mode}
            onChange={(e) =>
              setCrop((c) => ({ ...c, mode: e.target.value as "fit" | "fill" }))
            }
          >
            <option value="fit">Fit to frame</option>
            <option value="fill">Fill frame</option>
          </select>
        </label>
        {(["x", "y"] as const).map((axis) => (
          <label className="field" key={axis}>
            <span>Position {axis.toUpperCase()}</span>
            <input
              aria-label={`Crop position ${axis}`}
              type="range"
              min={0}
              max={1}
              step={0.01}
              disabled={!selected}
              value={crop[axis]}
              onChange={(e) =>
                setCrop((c) => ({ ...c, [axis]: Number(e.target.value) }))
              }
            />
            <span className="mono small">{crop[axis].toFixed(2)}</span>
          </label>
        ))}
        <Button
          className="wide"
          disabled={!selected || editor.busy}
          onClick={() =>
            void editor.transact("Adjust crop", [
              { type: "set_crop", segment_id: selected!.id, ...crop },
            ])
          }
        >
          Apply transform
        </Button>
      </Section>
      <Section title="Audio">
        {p.timeline.audio_warnings?.map((w, i) => (
          <p className="error-text" key={i}>
            {w}
          </p>
        ))}
        <label className="field">
          <span>Source audio</span>
          <select
            aria-label="Master audio source"
            value={p.timeline.master_audio_asset_id ?? ""}
            onChange={(e) =>
              void editor.transact("Set master audio", [
                { type: "set_audio_source", asset_id: e.target.value || null },
              ])
            }
          >
            <option value="">Silence</option>
            {Object.values(p.assets)
              .filter((a) => a.has_audio && a.status === "ready")
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
        <Numeric
          label="Gain ×"
          value={gain}
          min={0}
          max={4}
          step={0.1}
          onChange={setGain}
        />
        <Numeric
          label="Fade in (sec)"
          value={fadeIn}
          min={0}
          onChange={setFadeIn}
        />
        <Numeric
          label="Fade out (sec)"
          value={fadeOut}
          min={0}
          onChange={setFadeOut}
        />
        <div className="button-row">
          <Button
            disabled={!p.timeline.audio.length || editor.busy}
            onClick={() =>
              void editor.transact("Adjust source audio", [
                {
                  type: "set_audio_gain",
                  gain,
                  fade_in_us: microseconds(fadeIn),
                  fade_out_us: microseconds(fadeOut),
                },
              ])
            }
          >
            <Volume2 size={14} /> Apply audio
          </Button>
          <Button
            disabled={!p.timeline.audio.length || editor.busy}
            onClick={() =>
              void editor.transact("Toggle audio mute", [
                {
                  type: "set_audio_gain",
                  gain,
                  muted: !p.timeline.audio[0]?.muted,
                },
              ])
            }
          >
            {p.timeline.audio[0]?.muted ? "Unmute" : "Mute"}
          </Button>
        </div>
        {asset?.kind === "audio" && (
          <Button
            className="wide"
            disabled={editor.busy}
            onClick={() =>
              void editor.transact("Add music", [
                {
                  type: "add_music",
                  asset_id: asset.id,
                  output_in_us: 0,
                  source_in_us: 0,
                  source_out_us: asset.duration_us,
                  gain: 1,
                },
              ])
            }
          >
            <Music2 size={14} /> Add as music
          </Button>
        )}
        {p.timeline.music.map((m) => (
          <div className="field" key={m.id}>
            <span>{p.assets[m.asset_id]?.name}</span>
            <Button
              onClick={() =>
                void editor.transact("Remove music", [
                  { type: "remove_music", id: m.id },
                ])
              }
            >
              Remove
            </Button>
          </div>
        ))}
      </Section>
      <Section title="Captions">
        <p className="muted">
          Captions and transcription are unavailable in this release.
        </p>
      </Section>
      <Section title="History">
        {p.history
          .slice(-8)
          .reverse()
          .map((h, i) => (
            <div className="history-row" key={i}>
              <span>{h.label}</span>
              <small>{h.actor ?? "manual"}</small>
            </div>
          ))}
        {!p.history.length && (
          <p className="muted">Edits appear here with undo history.</p>
        )}
      </Section>
    </div>
  );
}
