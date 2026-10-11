import { describe, it, expect } from "vitest";
import {
  angleSourceAt,
  audioGainAt,
  eventAt,
  frameStep,
  outputDuration,
  segmentAt,
  sourceAt,
  timecode,
} from "./timeline";
import type { AudioSegment, Project, Segment } from "./types";
const first: Segment = {
  id: "a",
  asset_id: "A",
  source_in_us: 2_000_000,
  source_out_us: 5_000_000,
  output_in_us: 0,
  event_in_us: 4_000_000,
  crop: { mode: "fit", x: 0.5, y: 0.5 },
};
const second: Segment = {
  ...first,
  id: "b",
  source_in_us: 7_000_000,
  source_out_us: 9_000_000,
  output_in_us: 3_000_000,
  event_in_us: 9_000_000,
};
describe("canonical integer timeline mapping", () => {
  it("shows negative synchronized event time without clamping to zero", () => {
    expect(timecode(-2_500_000)).toBe("−00:02.500");
    expect(timecode(2_500_000)).toBe("00:02.500");
  });
  it("uses exclusive boundaries and maps cuts without interpolating source gaps", () => {
    expect(segmentAt([first, second], 3_000_000)?.id).toBe("b");
    expect(sourceAt(second, 3_000_000)).toBe(7_000_000);
    expect(eventAt(second, 3_000_000)).toBe(9_000_000);
    expect(segmentAt([first, second], 5_000_000)).toBeUndefined();
    expect(outputDuration([first, second])).toBe(5_000_000);
  });
  it("maps synchronized coverage using event time after reorder and rejects absent angles", () => {
    const p = {
      timeline: { video: [second, first] },
      sync: { offsets: { B: 5_000_000 } },
      assets: { B: { duration_us: 4_000_000 } },
    } as unknown as Project;
    expect(angleSourceAt(p, "B", 3_000_000)).toBeNull();
    expect(angleSourceAt(p, "B", 3_100_000)).toBeNull();
    expect(angleSourceAt(p, "B", 1_500_000)).toBe(500_000);
  });
  it("steps rational frames and clamps duration", () => {
    expect(frameStep(0, 1, { num: 30000, den: 1001 }, 10_000_000)).toBe(33367);
    expect(frameStep(0, -1, { num: 30, den: 1 }, 10)).toBe(0);
    expect(frameStep(9, 1, { num: 30, den: 1 }, 10)).toBe(10);
  });
  it("applies gain mute and fades against output time", () => {
    const a: AudioSegment = {
      ...first,
      gain: 2,
      muted: false,
      fade_in_us: 1_000_000,
      fade_out_us: 1_000_000,
    };
    expect(audioGainAt(a, 500_000)).toBe(1);
    expect(audioGainAt(a, 2_500_000)).toBe(1);
    expect(audioGainAt({ ...a, muted: true }, 1_500_000)).toBe(0);
  });
});
