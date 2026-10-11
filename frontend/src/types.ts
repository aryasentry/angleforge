export type FPS = { num: number; den: number };
export type Asset = {
  id: string;
  name: string;
  kind: "video" | "audio";
  status: "processing" | "ready" | "failed";
  duration_us: number;
  width: number;
  height: number;
  fps: FPS;
  has_audio: boolean;
  rotation: number;
  fingerprint: string;
  proxy: { source_origin_us: number; timebase: number };
  waveform: number[];
  error: string | null;
};
export type Segment = {
  id: string;
  asset_id: string;
  source_in_us: number;
  source_out_us: number;
  output_in_us: number;
  event_in_us: number;
  crop: { mode: "fit" | "fill"; x: number; y: number };
};
export type AudioSegment = {
  id: string;
  asset_id: string;
  source_in_us: number;
  source_out_us: number;
  output_in_us: number;
  gain: number;
  muted: boolean;
  fade_in_us: number;
  fade_out_us: number;
};
export type Sync = {
  reference_asset_id: string | null;
  offsets: Record<string, number>;
  confidence: Record<string, number>;
  anchors: unknown[];
  warnings: string[];
  edges: unknown[];
};
export type Project = {
  schema_version: 1;
  id: string;
  name: string;
  revision: number;
  timebase: number;
  settings: { width: number; height: number; fps: FPS };
  assets: Record<string, Asset>;
  sync: Sync;
  timeline: {
    video: Segment[];
    audio: AudioSegment[];
    captions: { id: string; start_us: number; end_us: number; text: string }[];
    master_audio_asset_id: string | null;
    audio_settings?: {
      gain: number;
      muted: boolean;
      fade_in_us: number;
      fade_out_us: number;
    };
    audio_warnings?: string[];
    music: AudioSegment[];
  };
  analysis: unknown[];
  history: {
    label: string;
    actor?: string;
    revision?: number;
    timestamp?: string;
  }[];
  can_undo: boolean;
  can_redo: boolean;
};
export type Operation = { type: string; [key: string]: unknown };
export type Plan = {
  proposal_job_id?: string;
  base_revision: number;
  label: string;
  operations: Operation[];
  summary: string;
  usage?: unknown;
  provider?: string;
  model?: string;
};
export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  progress: number | null;
  message: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};
export type ProjectSummary = {
  id: string;
  name: string;
  revision: number;
  updated_at: string;
};
