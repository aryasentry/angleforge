import { useState } from "react";
import {
  Scissors,
  Trash2,
  ZoomIn,
  ZoomOut,
  Film,
  Volume2,
  Music2,
} from "lucide-react";
import { media } from "../api";
import { duration, outputDuration, timecode, US } from "../timeline";
import type { Editor } from "../useEditor";
import type { Asset, AudioSegment } from "../types";
import { Button, IconButton } from "./Primitives";
export function Timeline({
  editor,
  time,
  setTime,
  selected,
  setSelected,
  split,
  remove,
}: {
  editor: Editor;
  time: number;
  setTime: (t: number) => void;
  selected: string | null;
  setSelected: (id: string) => void;
  split: () => void;
  remove: () => void;
}) {
  const [zoom, setZoom] = useState(70),
    p = editor.project;
  if (!p) return null;
  const total = outputDuration(p.timeline.video),
    width = Math.max(700, (total / US) * zoom),
    tick = Math.max(1, Math.ceil(70 / zoom));
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const bounds = e.currentTarget.getBoundingClientRect();
    setTime(
      Math.max(
        0,
        Math.min(
          total - 1,
          Math.round(((e.clientX - bounds.left) / zoom) * US),
        ),
      ),
    );
  };
  return (
    <section className="timeline panel">
      <div className="timeline-toolbar">
        <h2>Timeline</h2>
        <Button disabled={!selected || editor.busy} onClick={split}>
          <Scissors size={16} /> Split
        </Button>
        <Button disabled={!selected || editor.busy} onClick={remove}>
          <Trash2 size={15} /> Ripple delete
        </Button>
        <div className="zoom-controls">
          <IconButton
            label="Zoom out timeline"
            onClick={() => setZoom((z) => Math.max(12, z / 1.4))}
          >
            <ZoomOut size={16} />
          </IconButton>
          <input
            aria-label="Timeline zoom"
            type="range"
            min={12}
            max={220}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
          <IconButton
            label="Zoom in timeline"
            onClick={() => setZoom((z) => Math.min(220, z * 1.4))}
          >
            <ZoomIn size={16} />
          </IconButton>
        </div>
        <span className="timeline-revision">Revision {p.revision}</span>
      </div>
      <div className="timeline-body">
        <div className="track-labels">
          <div className="ruler-label" />
          <div>
            <Film size={17} /> V1 <span>Video</span>
          </div>
          <div>
            <Volume2 size={17} /> A1 <span>Master</span>
          </div>
          {p.timeline.music.length > 0 && (
            <div>
              <Music2 size={17} /> M1 <span>Music</span>
            </div>
          )}
        </div>
        <div className="timeline-scroll">
          <div className="timeline-canvas" style={{ width }}>
            <div className="timeline-ruler" onClick={seek}>
              {Array.from(
                { length: Math.floor(width / zoom / tick) + 1 },
                (_, i) => (
                  <span key={i} style={{ left: i * tick * zoom }}>
                    {timecode(i * tick * US).slice(0, 5)}
                  </span>
                ),
              )}
            </div>
            <div className="video-track" onClick={seek}>
              {p.timeline.video.map((s, index) => (
                <button
                  key={s.id}
                  className={`timeline-clip clip-color-${index % 3} ${selected === s.id ? "selected" : ""}`}
                  style={{
                    left: (s.output_in_us / US) * zoom,
                    width: Math.max(2, (duration(s) / US) * zoom),
                  }}
                  draggable
                  onDragStart={(e) =>
                    e.dataTransfer.setData("text/plain", s.id)
                  }
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    const id = e.dataTransfer.getData("text/plain");
                    if (!id || id === s.id) return;
                    const ids = p.timeline.video
                      .map((v) => v.id)
                      .filter((v) => v !== id);
                    ids.splice(ids.indexOf(s.id), 0, id);
                    void editor.transact("Reorder shots", [
                      { type: "reorder_segments", segment_ids: ids },
                    ]);
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelected(s.id);
                    const b = e.currentTarget.getBoundingClientRect();
                    setTime(
                      s.output_in_us +
                        Math.min(
                          duration(s) - 1,
                          Math.max(
                            0,
                            Math.round(((e.clientX - b.left) / zoom) * US),
                          ),
                        ),
                    );
                  }}
                >
                  <span className="clip-name">
                    {p.assets[s.asset_id]?.name}
                  </span>
                  <div className="clip-filmstrip">
                    {Array.from(
                      {
                        length: Math.max(
                          1,
                          Math.ceil(((duration(s) / US) * zoom) / 90),
                        ),
                      },
                      (_, i) => (
                        <img key={i} src={media(s.asset_id)} alt="" />
                      ),
                    )}
                  </div>
                </button>
              ))}
              {!total && (
                <div className="timeline-empty">
                  Append a clip from Media to create your first shot.
                </div>
              )}
            </div>
            <AudioTrack
              segments={p.timeline.audio}
              assets={p.assets}
              zoom={zoom}
              onClick={seek}
            />
            {p.timeline.music.length > 0 && (
              <AudioTrack
                segments={p.timeline.music}
                assets={p.assets}
                zoom={zoom}
                onClick={seek}
              />
            )}
            <div className="playhead" style={{ left: (time / US) * zoom }}>
              <span />
            </div>
          </div>
        </div>
      </div>
      <div className="timeline-footer">
        <span>
          Local media · {p.settings.fps.num / p.settings.fps.den} fps ·{" "}
          {p.settings.width} × {p.settings.height}
        </span>
        <span>
          Space play · ← → frame · S split · Delete ripple · ⌘/Ctrl Z undo
        </span>
      </div>
    </section>
  );
}
function AudioTrack({
  segments,
  assets,
  zoom,
  onClick,
}: {
  segments: AudioSegment[];
  assets: Record<string, Asset>;
  zoom: number;
  onClick: (e: React.MouseEvent<HTMLDivElement>) => void;
}) {
  return (
    <div className="audio-track" onClick={onClick}>
      {segments.map((s) => (
        <div
          className={`audio-clip ${s.muted ? "muted" : ""}`}
          key={s.id}
          style={{
            left: (s.output_in_us / US) * zoom,
            width: Math.max(2, (duration(s) / US) * zoom),
          }}
          title={`${assets[s.asset_id]?.name} · ${s.muted ? "Muted" : `${s.gain}× gain`}`}
        >
          <Waveform
            asset={assets[s.asset_id]}
            start={s.source_in_us}
            end={s.source_out_us}
          />
        </div>
      ))}
    </div>
  );
}
function Waveform({
  asset,
  start,
  end,
}: {
  asset: Asset;
  start: number;
  end: number;
}) {
  if (!asset?.waveform?.length)
    return <span className="silent-waveform">No waveform</span>;
  const values = asset.waveform.slice(
    Math.floor((start / asset.duration_us) * asset.waveform.length),
    Math.ceil((end / asset.duration_us) * asset.waveform.length),
  );
  const bins = 180;
  const points = Array.from({ length: bins }, (_, i) => {
    const begin = Math.floor((i / bins) * values.length),
      finish = Math.max(
        begin + 1,
        Math.floor(((i + 1) / bins) * values.length),
      );
    return Math.max(...values.slice(begin, finish), 0);
  });
  const max = Math.max(...points, 0.01);
  return (
    <svg
      viewBox={`0 0 ${bins} 60`}
      preserveAspectRatio="none"
      className="waveform"
      aria-label="Measured audio waveform"
    >
      <path
        d={points
          .map(
            (v, i) =>
              `M${i},${30 - (v / max) * 25}L${i},${30 + (v / max) * 25}`,
          )
          .join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth=".8"
      />
    </svg>
  );
}
