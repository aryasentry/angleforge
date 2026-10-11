import { useRef, useState } from "react";
import { Plus, Link2, Film, Music2, FlaskConical, Search } from "lucide-react";
import { api, media } from "../api";
import { timecode } from "../timeline";
import type { Editor } from "../useEditor";
import type { Job } from "../types";
import { Button } from "./Primitives";
export function Library({
  editor,
  selectedAsset,
  setSelectedAsset,
  onSyncJob,
}: {
  editor: Editor;
  selectedAsset: string | null;
  setSelectedAsset: (id: string) => void;
  onSyncJob: (id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null),
    [filter, setFilter] = useState("");
  const p = editor.project;
  const assets = Object.values(p?.assets ?? {}).filter((a) =>
    a.name.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <aside
      className="library panel"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        void editor.importFiles([...e.dataTransfer.files]);
      }}
    >
      <div className="panel-heading">
        <h2>Media</h2>
        <span>{assets.length} clips</span>
      </div>
      <div className="library-toolbar">
        <Button
          onClick={() => input.current?.click()}
          disabled={editor.busy || !p}
        >
          <Plus size={17} /> Import Media…
        </Button>
        <input
          ref={input}
          aria-label="Import media files"
          type="file"
          multiple
          accept="video/*,audio/*,.mov,.mp4,.m4a,.wav,.mp3"
          hidden
          onChange={(e) => {
            void editor.importFiles([...(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        {assets.length > 3 && (
          <label className="search">
            <Search size={14} />
            <input
              aria-label="Search media"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search clips"
            />
          </label>
        )}
      </div>
      <div className="asset-list">
        {assets.map((a, i) => (
          <div
            className={`asset-row ${selectedAsset === a.id ? "selected" : ""}`}
            key={a.id}
          >
            <button
              className="asset-select"
              onClick={() => setSelectedAsset(a.id)}
            >
              <div className="asset-thumbnail">
                {a.kind === "video" && a.status === "ready" ? (
                  <img src={media(a.id)} alt="" />
                ) : a.kind === "audio" ? (
                  <Music2 size={25} />
                ) : (
                  <Film size={25} />
                )}
                <span>{timecode(a.duration_us)}</span>
              </div>
              <div className="asset-meta">
                <strong>{a.name}</strong>
                <span>
                  {a.kind === "video" ? `${a.width} × ${a.height}` : "Audio"} ·{" "}
                  {a.has_audio ? "Audio" : "Silent"}
                </span>
                <span className={`asset-status ${a.status}`}>
                  <i />
                  {a.status === "ready" ? "Ready" : a.status}
                </span>
              </div>
            </button>
            <Button
              className="append-button"
              title={`Append ${a.name} to the timeline`}
              disabled={
                a.status !== "ready" || a.kind !== "video" || editor.busy
              }
              onClick={() =>
                void editor.transact(`Append ${a.name}`, [
                  {
                    type: "insert_segment",
                    asset_id: a.id,
                    source_in_us: 0,
                    source_out_us: a.duration_us,
                  },
                ])
              }
            >
              <Plus size={13} /> Append
            </Button>
            {a.error && <p className="error-text">{a.error}</p>}
          </div>
        ))}
        {!assets.length && (
          <div className="library-empty">
            <Film size={27} />
            <h3>Bring your angles together</h3>
            <p>
              Drop MP4, MOV or audio files here. Media stays on this machine.
            </p>
            <Button onClick={() => void editor.demo()} disabled={editor.busy}>
              <FlaskConical size={15} /> Load synthetic demo
            </Button>
            <small>Three labeled cameras with known offsets.</small>
          </div>
        )}
      </div>
      <div className="library-bottom">
        <Button
          disabled={
            editor.busy ||
            assets.filter((a) => a.has_audio && a.status === "ready").length < 2
          }
          onClick={() =>
            void editor.run(async () => {
              const r = await api<{ job: Job }>(`/projects/${p!.id}/sync`, {
                base_revision: p!.revision,
              });
              onSyncJob(r.job.id);
            })
          }
        >
          <Link2 size={17} /> Synchronize ({assets.length} clips)
        </Button>
        <p>Measured audio alignment · review before applying</p>
      </div>
    </aside>
  );
}
