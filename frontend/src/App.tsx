import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  FilePlus,
  FolderOpen,
  Redo2,
  Save,
  Triangle,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import { api } from "./api";
import { frameStep, outputDuration, segmentAt, timecode } from "./timeline";
import { useEditor } from "./useEditor";
import { Button, IconButton } from "./components/Primitives";
import { Library } from "./components/Library";
import { Preview } from "./components/Preview";
import { Timeline } from "./components/Timeline";
import { Inspector } from "./components/Inspector";
import { Director } from "./components/Director";
import { Jobs } from "./components/Jobs";
export default function App() {
  const editor = useEditor(),
    p = editor.project,
    [time, setTimeState] = useState(0),
    [playing, setPlaying] = useState(false),
    [selected, setSelected] = useState<string | null>(null),
    [selectedAsset, setSelectedAsset] = useState<string | null>(null),
    [tab, setTab] = useState("inspector"),
    [syncJob, setSyncJob] = useState<string | null>(null),
    [left, setLeft] = useState(230),
    [right, setRight] = useState(300),
    [timelineHeight, setTimelineHeight] = useState(() =>
      window.matchMedia("(min-width: 801px) and (max-height: 800px)").matches
        ? 220
        : 260,
    ),
    [projectsOpen, setProjectsOpen] = useState(false),
    [exportOpen, setExportOpen] = useState(false),
    [exportName, setExportName] = useState("my-edit.mp4"),
    [renaming, setRenaming] = useState(false),
    [projectName, setProjectName] = useState("");
  const shell = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const compact = window.matchMedia(
      "(min-width: 801px) and (max-height: 800px)",
    );
    const update = () =>
      setTimelineHeight((height) =>
        height === 220 || height === 260
          ? compact.matches
            ? 220
            : 260
          : height,
      );
    compact.addEventListener("change", update);
    return () => compact.removeEventListener("change", update);
  }, []);
  const setTime = useCallback((t: number) => setTimeState(Math.round(t)), []);
  const selectedSegment = p?.timeline.video.find((s) => s.id === selected),
    active =
      selectedSegment ?? (p ? segmentAt(p.timeline.video, time) : undefined);
  const split = () => {
    if (!p || !active) return;
    void editor
      .transact("Split shot", [
        { type: "split_segment", segment_id: active.id, at_us: time },
      ])
      .then((result) => {
        if (result)
          setSelected(segmentAt(result.timeline.video, time)?.id ?? null);
      });
  };
  const remove = () => {
    if (!p || !active) return;
    void editor
      .transact("Ripple delete shot", [
        { type: "remove_segment", segment_id: active.id, ripple: true },
      ])
      .then((result) => {
        if (result) {
          setTime(
            Math.min(
              time,
              Math.max(0, outputDuration(result.timeline.video) - 1),
            ),
          );
          setSelected(null);
        }
      });
  };
  useEffect(() => {
    setPlaying(false);
    setTime(0);
    setSelected(null);
    setSelectedAsset(null);
    setSyncJob(null);
  }, [p?.id]);
  useEffect(() => {
    if (
      p &&
      time >= outputDuration(p.timeline.video) &&
      outputDuration(p.timeline.video) > 0
    )
      setTime(outputDuration(p.timeline.video) - 1);
  }, [p?.revision]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.target as HTMLElement).closest(
          'input,textarea,select,[contenteditable="true"]',
        )
      )
        return;
      if (!p) return;
      const total = outputDuration(p.timeline.video);
      if (e.code === "Space") {
        e.preventDefault();
        if (total) {
          if (!playing && time >= total - 2000) setTime(0);
          setPlaying((v) => !v);
        }
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        setTime(
          frameStep(
            time,
            e.key === "ArrowLeft" ? -1 : 1,
            p.settings.fps,
            Math.max(0, total - 1),
          ),
        );
      } else if (e.key.toLowerCase() === "s" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        split();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        remove();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        void editor.history(e.shiftKey ? "redo" : "undo");
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void editor.save();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [p, time, active?.id]);
  const resize = (
    e: React.PointerEvent,
    which: "left" | "right" | "timeline",
  ) => {
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY, left, right, timelineHeight };
    e.currentTarget.setPointerCapture(e.pointerId);
    const target = e.currentTarget;
    const move = (event: PointerEvent) => {
      if (which === "left")
        setLeft(
          Math.min(390, Math.max(180, start.left + event.clientX - start.x)),
        );
      if (which === "right")
        setRight(
          Math.min(450, Math.max(260, start.right - event.clientX + start.x)),
        );
      if (which === "timeline")
        setTimelineHeight(
          Math.min(
            420,
            Math.max(190, start.timelineHeight - event.clientY + start.y),
          ),
        );
    };
    const up = () => {
      target.removeEventListener("pointermove", move as EventListener);
      target.removeEventListener("pointerup", up);
    };
    target.addEventListener("pointermove", move as EventListener);
    target.addEventListener("pointerup", up, { once: true });
  };
  return (
    <div
      className="app-shell"
      ref={shell}
      style={
        {
          "--library-width": `${left}px`,
          "--inspector-width": `${right}px`,
          "--timeline-height": `${timelineHeight}px`,
        } as React.CSSProperties
      }
    >
      <header className="app-header">
        <div className="brand">
          <Triangle size={23} fill="currentColor" />
          <strong>AngleForge</strong>
        </div>
        <div className="project-picker">
          <Button
            onClick={() => {
              setProjectsOpen((v) => !v);
              setRenaming(false);
            }}
            disabled={!p}
          >
            {p?.name ?? "Opening editor…"}
            <ChevronDown size={14} />
          </Button>
          {projectsOpen && (
            <div className="project-menu">
              <Button
                onClick={() => {
                  void editor.create();
                  setProjectsOpen(false);
                }}
              >
                <FilePlus size={14} /> New project
              </Button>
              {editor.projects.map((project) => (
                <Button
                  key={project.id}
                  onClick={() => {
                    void editor.open(project.id);
                    setProjectsOpen(false);
                  }}
                >
                  <FolderOpen size={14} />
                  {project.name}
                  <span>r{project.revision}</span>
                </Button>
              ))}
              {p && (
                <Button
                  onClick={() => {
                    setProjectName(p.name);
                    setRenaming(true);
                  }}
                >
                  Rename project
                </Button>
              )}
              {p && renaming && (
                <form
                  className="rename-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!projectName.trim()) return;
                    void editor
                      .transact("Rename project", [
                        { type: "rename_project", name: projectName.trim() },
                      ])
                      .then((result) => {
                        if (result) {
                          setProjectsOpen(false);
                          setRenaming(false);
                          void editor.save();
                        }
                      });
                  }}
                >
                  <label htmlFor="project-name">Project name</label>
                  <input
                    id="project-name"
                    aria-label="Project name"
                    value={projectName}
                    autoFocus
                    maxLength={160}
                    onChange={(e) => setProjectName(e.target.value)}
                  />
                  <Button
                    className="accent wide"
                    type="submit"
                    disabled={editor.busy || !projectName.trim()}
                  >
                    Save name
                  </Button>
                </form>
              )}
            </div>
          )}
        </div>
        <div className="save-status">
          <CheckCircle2 size={15} />
          {editor.saved}
        </div>
        <div className="header-actions">
          <Button
            disabled={!p?.can_undo || editor.busy}
            onClick={() => void editor.history("undo")}
          >
            <Undo2 size={16} /> Undo
          </Button>
          <Button
            disabled={!p?.can_redo || editor.busy}
            onClick={() => void editor.history("redo")}
          >
            <Redo2 size={16} /> Redo
          </Button>
          <IconButton
            label="Save project (Ctrl/Cmd S)"
            disabled={!p || editor.busy}
            onClick={() => void editor.save()}
          >
            <Save size={16} />
          </IconButton>
          <Button
            className="accent export-button"
            disabled={!p?.timeline.video.length || editor.busy}
            onClick={() => setExportOpen(true)}
          >
            <Upload size={16} /> Export
          </Button>
        </div>
      </header>
      {editor.error && (
        <div role="alert" className="error-banner">
          <span>{editor.error}</span>
          <IconButton label="Dismiss error" onClick={() => editor.setError("")}>
            <X size={14} />
          </IconButton>
        </div>
      )}
      <main className="workspace">
        <Library
          editor={editor}
          selectedAsset={selectedAsset}
          setSelectedAsset={(id) => {
            setSelectedAsset(id);
            setSelected(null);
          }}
          onSyncJob={(id) => {
            setSyncJob(id);
            setTab("director");
          }}
        />
        <div
          className="resize-handle vertical"
          role="separator"
          aria-label="Resize media library"
          onPointerDown={(e) => resize(e, "left")}
        />
        {p ? (
          <Preview
            project={p}
            time={time}
            setTime={setTime}
            playing={playing}
            setPlaying={setPlaying}
            onSwitch={(assetId) => {
              if (active)
                void editor.transact("Switch source angle", [
                  {
                    type: "switch_angle",
                    asset_id: assetId,
                    start_us: active.output_in_us,
                    end_us:
                      active.output_in_us +
                      active.source_out_us -
                      active.source_in_us,
                  },
                ]);
            }}
          />
        ) : (
          <div className="connecting">
            <p>
              {editor.error
                ? "Waiting for the local backend."
                : "Opening your local workspace…"}
            </p>
          </div>
        )}
        <div
          className="resize-handle vertical"
          role="separator"
          aria-label="Resize inspector"
          onPointerDown={(e) => resize(e, "right")}
        />
        <aside className="right-panel panel">
          <div className="panel-tabs">
            <button
              className={tab === "inspector" ? "active" : ""}
              onClick={() => setTab("inspector")}
            >
              Inspector
            </button>
            <button
              className={tab === "director" ? "active" : ""}
              onClick={() => setTab("director")}
            >
              Director
            </button>
            <button
              className={tab === "jobs" ? "active" : ""}
              onClick={() => setTab("jobs")}
            >
              Jobs
              {editor.jobs.some((j) =>
                ["queued", "running"].includes(j.status),
              ) && <i />}
            </button>
          </div>
          <div className="right-scroll">
            {p && (
              <>
                <div hidden={tab !== "inspector"}>
                  <Inspector
                    editor={editor}
                    selected={selectedSegment}
                    selectedAsset={selectedAsset}
                  />
                </div>
                <div hidden={tab !== "director"}>
                  <Director editor={editor} syncJob={syncJob} />
                </div>
                <div hidden={tab !== "jobs"}>
                  <Jobs editor={editor} />
                </div>
              </>
            )}
            {tab !== "jobs" &&
              editor.jobs.some(
                (j) => j.status === "running" || j.status === "queued",
              ) && (
                <div
                  className="active-job-summary"
                  onClick={() => setTab("jobs")}
                  role="button"
                  tabIndex={0}
                >
                  {editor.jobs
                    .filter(
                      (j) => j.status === "running" || j.status === "queued",
                    )
                    .map((j) => (
                      <p key={j.id}>
                        {j.kind} ·{" "}
                        {j.progress === null
                          ? "working"
                          : `${Math.round(j.progress * 100)}%`}{" "}
                        · {j.message}
                      </p>
                    ))}
                </div>
              )}
          </div>
        </aside>
      </main>
      <div
        className="resize-handle horizontal"
        role="separator"
        aria-label="Resize timeline"
        onPointerDown={(e) => resize(e, "timeline")}
      />
      <Timeline
        editor={editor}
        time={time}
        setTime={setTime}
        selected={selected}
        setSelected={setSelected}
        split={split}
        remove={remove}
      />
      {exportOpen && p && (
        <div className="modal-backdrop" onClick={() => setExportOpen(false)}>
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="export-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <h2 id="export-title">Export your edit</h2>
              <IconButton
                label="Close export"
                onClick={() => setExportOpen(false)}
              >
                <X size={17} />
              </IconButton>
            </div>
            <p>
              Original media · {p.settings.width} × {p.settings.height} ·{" "}
              {timecode(outputDuration(p.timeline.video))} · revision{" "}
              {p.revision}
            </p>
            <label className="field">
              <span>Filename</span>
              <input
                aria-label="Export filename"
                value={exportName}
                onChange={(e) => setExportName(e.target.value)}
              />
            </label>
            <p className="muted">
              Saved in the local data/exports folder. The download appears after
              FFmpeg verification.
            </p>
            <Button
              className="accent wide"
              disabled={editor.busy || !exportName.trim()}
              onClick={() =>
                void editor.run(async () => {
                  await api(`/projects/${p.id}/export`, {
                    base_revision: p.revision,
                    name: exportName,
                  });
                  setExportOpen(false);
                  setTab("jobs");
                })
              }
            >
              <Upload size={15} /> Render MP4
            </Button>
            <a
              className="document-link"
              href={`/api/projects/${p.id}/document`}
            >
              Download project document
            </a>
          </section>
        </div>
      )}
    </div>
  );
}
