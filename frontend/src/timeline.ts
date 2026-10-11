import type { AudioSegment, FPS, Project, Segment } from "./types";
export const US = 1_000_000;
export const duration = (s: Segment | AudioSegment) =>
  s.source_out_us - s.source_in_us;
export const outputDuration = (segments: Segment[]) =>
  segments.reduce((end, s) => Math.max(end, s.output_in_us + duration(s)), 0);
export const segmentAt = <T extends Segment | AudioSegment>(
  segments: T[],
  time: number,
): T | undefined =>
  segments.find(
    (s) => time >= s.output_in_us && time < s.output_in_us + duration(s),
  );
export const sourceAt = (s: Segment | AudioSegment, time: number) =>
  s.source_in_us + time - s.output_in_us;
export const eventAt = (s: Segment, time: number) =>
  s.event_in_us + time - s.output_in_us;
export const angleSourceAt = (p: Project, assetId: string, time: number) => {
  const s = segmentAt(p.timeline.video, time);
  if (!s) return null;
  if (!(assetId in p.sync.offsets)) return null;
  const source = eventAt(s, time) - p.sync.offsets[assetId];
  const a = p.assets[assetId];
  return a && source >= 0 && source < a.duration_us ? source : null;
};
export const frameStep = (
  time: number,
  direction: number,
  fps: FPS,
  max: number,
) =>
  Math.max(
    0,
    Math.min(max, Math.round(time + (direction * US * fps.den) / fps.num)),
  );
export const timecode = (time: number) => {
  const ms = Math.floor(Math.abs(time) / 1000);
  return `${time < 0 ? "−" : ""}${Math.floor(ms / 60000)
    .toString()
    .padStart(
      2,
      "0",
    )}:${Math.floor(ms / 1000) % 60 < 10 ? "0" : ""}${Math.floor(ms / 1000) % 60}.${(ms % 1000).toString().padStart(3, "0")}`;
};
export const seconds = (us: number) => Number((us / US).toFixed(3));
export const microseconds = (seconds: number) => Math.round(seconds * US);
export const audioGainAt = (s: AudioSegment, time: number) => {
  if (s.muted) return 0;
  const elapsed = time - s.output_in_us,
    remaining = duration(s) - elapsed;
  return (
    s.gain *
    Math.min(
      1,
      s.fade_in_us ? Math.max(0, elapsed / s.fade_in_us) : 1,
      s.fade_out_us ? Math.max(0, remaining / s.fade_out_us) : 1,
    )
  );
};
