import { useEffect, useRef, useState } from "react";
import {
  ChevronFirst,
  ChevronLast,
  Maximize,
  Pause,
  Play,
  Volume2,
  Film,
} from "lucide-react";
import { media } from "../api";
import {
  angleSourceAt,
  audioGainAt,
  duration,
  eventAt,
  frameStep,
  outputDuration,
  segmentAt,
  sourceAt,
  timecode,
  US,
} from "../timeline";
import type { AudioSegment, Project } from "../types";
import { IconButton } from "./Primitives";
export function Preview({
  project,
  time,
  setTime,
  playing,
  setPlaying,
  onSwitch,
}: {
  project: Project;
  time: number;
  setTime: (t: number) => void;
  playing: boolean;
  setPlaying: (v: boolean) => void;
  onSwitch: (id: string) => void;
}) {
  const video = useRef<HTMLVideoElement>(null),
    screen = useRef<HTMLDivElement>(null),
    audio = useRef(new Map<string, HTMLAudioElement>()),
    raf = useRef(0),
    clock = useRef({ time: 0, at: 0 }),
    latest = useRef({ project, time, playing });
  latest.current = { project, time, playing };
  const audioContext = useRef<AudioContext | null>(null);
  const [frameSize, setFrameSize] = useState({ width: 640, height: 360 });
  const [monitor, setMonitor] = useState(0.8),
    [mediaError, setMediaError] = useState(""),
    [audioNotice, setAudioNotice] = useState("");
  const current = segmentAt(project.timeline.video, time),
    total = outputDuration(project.timeline.video);
  const syncAudio = (
    segments: AudioSegment[],
    timeUs: number,
    isPlaying: boolean,
  ) => {
    const active = new Set<string>();
    for (const s of segments) {
      if (timeUs < s.output_in_us || timeUs >= s.output_in_us + duration(s))
        continue;
      active.add(s.id);
      let a = audio.current.get(s.id);
      if (!a) {
        a = new Audio(media(s.asset_id, "monitor"));
        a.preload = "auto";
        const element = a;
        element.addEventListener("error", () => {
          if (element.src.endsWith("/monitor")) {
            setAudioNotice("Legacy audio proxy · reimport to preserve stereo monitoring");
            element.src = media(s.asset_id, "audio");
            element.load();
          } else {
            setAudioNotice("Audio proxy unavailable · check the import job");
          }
        });
        audio.current.set(s.id, a);
      }
      const target = sourceAt(s, timeUs) / US;
      if (Math.abs(a.currentTime - target) > 0.08) a.currentTime = target;
      const gain = audioGainAt(s, timeUs) * monitor; // WebAudio permits gains above 1; HTML media volume is capped.
      if (!("audioContext" in a)) {
        const context =
          audioContext.current ?? (audioContext.current = new AudioContext());
        const node = context.createMediaElementSource(a);
        const gainNode = context.createGain();
        node.connect(gainNode).connect(context.destination);
        Object.assign(a, { audioContext: context, gainNode });
      }
      const extended = a as HTMLAudioElement & {
        audioContext: AudioContext;
        gainNode: GainNode;
      };
      extended.gainNode.gain.value = gain;
      if (isPlaying) {
        void extended.audioContext.resume();
        if (a.paused) void a.play().catch(() => {});
      } else a.pause();
    }
    for (const [id, a] of audio.current) if (!active.has(id)) a.pause();
  };
  useEffect(() => {
    clock.current = { time, at: performance.now() };
    const v = video.current;
    if (v && current) {
      const target = sourceAt(current, time) / US;
      if (Math.abs(v.currentTime - target) > 0.07) v.currentTime = target;
      if (playing) {
        if (v.paused)
          void v.play().catch((e) => {
            if (e.name !== "AbortError") {
              setMediaError(e.message);
              setPlaying(false);
            }
          });
      } else v.pause();
    }
    syncAudio(
      [...project.timeline.audio, ...project.timeline.music],
      time,
      playing,
    );
  }, [time, current?.id, playing, project.revision, monitor]);
  useEffect(() => {
    if (!playing) return;
    const tick = (now: number) => {
      const { project: p } = latest.current;
      const end = outputDuration(p.timeline.video);
      const t = Math.round(
        clock.current.time + (now - clock.current.at) * 1000,
      );
      if (t >= end) {
        setTime(Math.max(0, end - 1));
        setPlaying(false);
        return;
      }
      setTime(t);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, setTime, setPlaying]);
  useEffect(
    () => () => {
      for (const a of audio.current.values()) {
        a.pause();
        a.src = "";
      }
      audio.current.clear();
      void audioContext.current?.close();
    },
    [],
  );
  useEffect(() => {
    const el = screen.current;
    if (!el) return;
    const resize = () => {
      const box = el.getBoundingClientRect(),
        ratio = project.settings.width / project.settings.height;
      const w = Math.max(
        1,
        Math.min(box.width - 32, (box.height - 10) * ratio),
      );
      setFrameSize({ width: w, height: w / ratio });
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, [project.settings.width, project.settings.height]);
  const seek = (t: number) => {
    setMediaError("");
    setTime(Math.max(0, Math.min(total - 1, t)));
  };
  return (
    <section className="viewer-panel">
      <div className="viewer-label">
        <span>Program preview</span>
        <span>
          {project.settings.width} × {project.settings.height} ·{" "}
          {Number(
            (project.settings.fps.num / project.settings.fps.den).toFixed(2),
          )}{" "}
          fps
        </span>
      </div>
      <div className="preview-stage" ref={screen}>
        <div
          className="output-frame"
          style={{ width: frameSize.width, height: frameSize.height }}
        >
          {current ? (
            <video
              ref={video}
              key={current.asset_id}
              src={media(current.asset_id, "proxy")}
              muted
              playsInline
              preload="auto"
              style={{
                objectFit: current.crop.mode === "fit" ? "contain" : "cover",
                objectPosition:
                  current.crop.mode === "fit"
                    ? "50% 50%"
                    : `${current.crop.x * 100}% ${current.crop.y * 100}%`,
              }}
              onLoadedMetadata={() => {
                if (video.current) {
                  video.current.currentTime = sourceAt(current, time) / US;
                  if (playing) void video.current.play();
                }
              }}
              onError={() => {
                setMediaError("Proxy unavailable. Check the import job.");
                setPlaying(false);
              }}
            />
          ) : (
            <div className="preview-empty">
              <Film size={34} />
              <h2>Your edit starts here</h2>
              <p>Import footage, then append a camera to the timeline.</p>
            </div>
          )}
          {project.timeline.captions
            .filter((c) => time >= c.start_us && time < c.end_us)
            .map((c) => (
              <div className="caption-overlay" key={c.id}>
                {c.text}
              </div>
            ))}
        </div>
        {mediaError && <div className="preview-error">{mediaError}</div>}
      </div>
      <div className="transport">
        <span className="timecode">
          {timecode(time)} <span>/ {timecode(total)}</span>
        </span>
        <div className="transport-main">
          <IconButton
            label="Previous frame (Left arrow)"
            disabled={!total}
            onClick={() =>
              seek(frameStep(time, -1, project.settings.fps, total - 1))
            }
          >
            <ChevronFirst size={17} />
          </IconButton>
          <IconButton
            label={playing ? "Pause (Space)" : "Play (Space)"}
            disabled={!total}
            onClick={() => {
              if (!playing && time >= total - 2000) seek(0);
              setPlaying(!playing);
            }}
          >
            {playing ? <Pause size={20} /> : <Play size={20} />}
          </IconButton>
          <IconButton
            label="Next frame (Right arrow)"
            disabled={!total}
            onClick={() =>
              seek(frameStep(time, 1, project.settings.fps, total - 1))
            }
          >
            <ChevronLast size={17} />
          </IconButton>
        </div>
        <label className="monitor-volume" title="Preview monitor volume">
          <Volume2 size={16} />
          <input
            aria-label="Preview volume"
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={monitor}
            onChange={(e) => setMonitor(Number(e.target.value))}
          />
        </label>
        <IconButton
          label="Fullscreen preview"
          onClick={() => void screen.current?.requestFullscreen()}
        >
          <Maximize size={17} />
        </IconButton>
      </div>
      <input
        className="preview-seek"
        aria-label="Preview seek"
        type="range"
        min={0}
        max={Math.max(1, total - 1)}
        step={1000}
        value={Math.min(time, total - 1) || 0}
        disabled={!total}
        onChange={(e) => seek(Number(e.target.value))}
      />
      <div className="angle-rail">
        {Object.values(project.assets)
          .filter((a) => a.kind === "video" && a.status === "ready")
          .map((a, index) => (
            <Angle
              key={a.id}
              id={a.id}
              name={a.name}
              index={index}
              source={angleSourceAt(project, a.id, time)}
              aligned={a.id in project.sync.offsets}
              active={current?.asset_id === a.id}
              disabled={!current}
              onSwitch={() => onSwitch(a.id)}
            />
          ))}
        {!Object.values(project.assets).some((a) => a.kind === "video") && (
          <div className="rail-empty">
            Synchronized angles appear here after import.
          </div>
        )}
      </div>
      <div className="viewer-footer">
        {audioNotice && <span>{audioNotice} · </span>}
        {current ? (
          <>
            Event {timecode(eventAt(current, time))} · Source{" "}
            {timecode(sourceAt(current, time))}
          </>
        ) : (
          <>Local proxies · Originals preserved</>
        )}
      </div>
    </section>
  );
}
function Angle({
  id,
  name,
  index,
  source,
  aligned,
  active,
  disabled,
  onSwitch,
}: {
  id: string;
  name: string;
  index: number;
  source: number | null;
  aligned: boolean;
  active: boolean;
  disabled: boolean;
  onSwitch: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (
      ref.current &&
      source !== null &&
      Math.abs(ref.current.currentTime - source / US) > 0.12
    )
      ref.current.currentTime = source / US;
  }, [source]);
  return (
    <button
      className={`angle ${active ? "active" : ""} ${source === null ? "unavailable" : ""}`}
      disabled={disabled || source === null}
      title={
        source === null
          ? aligned
            ? "No source coverage at this event moment"
            : "Unaligned — synchronize or set a manual offset"
          : `Switch selected shot to ${name}`
      }
      onClick={onSwitch}
    >
      <div className="angle-picture">
        {source !== null ? (
          <video
            ref={ref}
            muted
            playsInline
            preload="metadata"
            src={media(id, "proxy")}
            onLoadedMetadata={() => {
              if (ref.current && source !== null)
                ref.current.currentTime = source / US;
            }}
          />
        ) : (
          <div>{aligned ? "No coverage" : "Unaligned"}</div>
        )}
      </div>
      <div className="angle-label">
        <span className="angle-letter">{String.fromCharCode(65 + index)}</span>
        <span>{name}</span>
        <span>{source === null ? "—" : timecode(source)}</span>
      </div>
    </button>
  );
}
