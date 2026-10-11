import { useEffect, useState } from "react";
import { ArrowRight, Check, Link2, ShieldCheck, X } from "lucide-react";
import { api } from "../api";
import { microseconds, seconds } from "../timeline";
import type { Job, Plan } from "../types";
import type { Editor } from "../useEditor";
import { Button, Numeric, Section } from "./Primitives";
type ProviderStatus = {
  default_provider: string;
  default_model: string;
  configured: boolean;
  providers: { id: string; configured: boolean; key_env: string }[];
  max_tokens: number;
  disclosure: string;
};
type Model = {
  id: string;
  name: string;
  planning_supported: boolean;
  input_modalities: string[];
};
export function Director({
  editor,
  syncJob,
}: {
  editor: Editor;
  syncJob: string | null;
}) {
  const p = editor.project!,
    [prompt, setPrompt] = useState(""),
    [mode, setMode] = useState("local"),
    [status, setStatus] = useState<ProviderStatus | null>(null),
    [model, setModel] = useState(""),
    [models, setModels] = useState<Model[]>([]),
    [consent, setConsent] = useState(false),
    [frames, setFrames] = useState(false),
    [budget, setBudget] = useState(1200),
    [plan, setPlan] = useState<Plan | null>(null),
    [planningJob, setPlanningJob] = useState<string | null>(null),
    [offsets, setOffsets] = useState<Record<string, number>>({}),
    [reference, setReference] = useState(""),
    [anchorAsset, setAnchorAsset] = useState(""),
    [referenceSec, setReferenceSec] = useState(0),
    [anchorSec, setAnchorSec] = useState(0),
    [aiAssets, setAiAssets] = useState<string[]>([]),
    [anchors, setAnchors] = useState<unknown[]>([]);
  useEffect(() => {
    void api<ProviderStatus>("/provider")
      .then((s) => {
        setStatus(s);
        setModel(s.default_model);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    setOffsets(
      Object.fromEntries(
        Object.values(p.assets).map((a) => [
          a.id,
          seconds(p.sync.offsets[a.id] ?? 0),
        ]),
      ),
    );
    setReference(p.sync.reference_asset_id ?? Object.keys(p.assets)[0] ?? "");
    setAiAssets(Object.keys(p.assets));
    setAnchors(p.sync.anchors);
    setConsent(false);
    setFrames(false);
  }, [p.id, p.revision]);
  useEffect(() => {
    if (mode === "local") {
      setModels([]);
      return;
    }
    void editor.run(async () => {
      const r = await api<{ models: Model[] }>(
        `/provider/models?provider=${mode}`,
      );
      setModels(r.models);
    });
  }, [mode]);
  useEffect(() => {
    const job = editor.jobs.find((j) => j.id === planningJob);
    if (job?.status === "succeeded" && job.result) {
      setPlan(job.result as unknown as Plan);
      setPlanningJob(null);
    }
    if (job?.status === "failed" || job?.status === "canceled")
      setPlanningJob(null);
  }, [editor.jobs, planningJob]);
  const sync = editor.jobs.find((j) => j.id === syncJob),
    syncResult = sync?.status === "succeeded" ? sync.result : null;
  const submit = () =>
    void editor.run(async () => {
      setPlan(null);
      if (mode === "local") {
        setPlan(
          await api<Plan>(`/projects/${p.id}/command`, {
            base_revision: p.revision,
            prompt,
          }),
        );
      } else {
        const r = await api<{ job: Job }>(`/projects/${p.id}/ai/plan`, {
          base_revision: p.revision,
          prompt,
          provider: mode,
          model,
          consent: { metadata: consent, frames },
          asset_ids: aiAssets,
          max_tokens: budget,
        });
        setPlanningJob(r.job.id);
      }
    });
  const applySync = () => {
    if (!syncResult) return;
    const fields = [
      "reference_asset_id",
      "offsets",
      "confidence",
      "anchors",
      "warnings",
      "edges",
    ];
    void editor.transact(
      "Apply measured synchronization",
      [
        {
          type: "set_sync",
          ...Object.fromEntries(
            fields
              .filter((k) => k in syncResult)
              .map((k) => [k, syncResult[k]]),
          ),
        },
      ],
      "manual",
      typeof syncResult.base_revision === "number"
        ? syncResult.base_revision
        : undefined,
    );
  };
  const manualSync = (anchorValues: unknown[] = anchors) =>
    void editor.transact("Apply manual synchronization", [
      {
        type: "set_sync",
        reference_asset_id: reference,
        offsets: Object.fromEntries(
          Object.entries(offsets).map(([id, value]) => [
            id,
            microseconds(value),
          ]),
        ),
        confidence: {},
        anchors: anchorValues,
        warnings: [],
        edges: [],
      },
    ]);
  return (
    <div className="director-content">
      <Section title="Assistant">
        <p className="muted">
          Make a precise change, review the proposal, then apply it.
        </p>
        <label className="field">
          <span>Mode</span>
          <select
            aria-label="Director mode"
            value={mode}
            onChange={(e) => {
              setMode(e.target.value);
              setConsent(false);
              setFrames(false);
            }}
          >
            <option value="local">Offline commands</option>
            <option value="openrouter">OpenRouter</option>
            <option value="zen">OpenCode Zen</option>
          </select>
        </label>
        {mode === "local" ? (
          <div className="local-disclosure">
            <ShieldCheck size={15} />
            <span>Offline command grammar · no model call</span>
          </div>
        ) : (
          <>
            <label className="field">
              <span>Model ID</span>
              <input
                aria-label="AI model ID"
                list="models"
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  setConsent(false);
                  setFrames(false);
                }}
                placeholder="Choose a configured model"
              />
              <datalist id="models">
                {models
                  .filter((m) => m.planning_supported)
                  .map((m) => (
                    <option value={m.id} key={m.id}>
                      {m.name}
                    </option>
                  ))}
              </datalist>
            </label>
            <Numeric
              label="Max output tokens"
              value={budget}
              min={1}
              max={status?.max_tokens ?? 1600}
              step={100}
              onChange={setBudget}
            />
            <div className="external-disclosure">
              <strong>
                {mode} · {model || "model required"}
              </strong>
              <p>
                {status?.disclosure ??
                  "Selected asset names, timings, local analysis metadata and your instruction leave this machine. No frames, audio or video are sent."}
              </p>
              <p>
                Provider billing is separate. Transcription is unavailable. Full
                video and audio are never sent by this planner.
              </p>
              {!status?.providers.find((s) => s.id === mode)?.configured && (
                <p className="error-text">
                  Provider key is not configured in the backend. Manual editing
                  and offline commands are available.
                </p>
              )}
              <fieldset className="asset-consent">
                <legend>Assets shared as metadata</legend>
                {Object.values(p.assets).map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={aiAssets.includes(a.id)}
                      onChange={(e) => {
                        setAiAssets((ids) =>
                          e.target.checked
                            ? [...ids, a.id]
                            : ids.filter((id) => id !== a.id),
                        );
                        setConsent(false);
                      }}
                    />
                    {a.name}
                  </label>
                ))}
              </fieldset>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={frames}
                  disabled={
                    mode !== "openrouter" ||
                    !models
                      .find((m) => m.id === model)
                      ?.input_modalities.includes("image")
                  }
                  onChange={(e) => {
                    setFrames(e.target.checked);
                    setConsent(false);
                  }}
                />
                Also send sparse JPEG samples with source timestamps (up to 4
                per selected clip, 12 total). Samples can miss action.
              </label>
              {frames && (
                <p>
                  Choose 1–3 ready video clips below. Only a model with verified
                  image support can receive frames.
                </p>
              )}
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                />
                I approve this provider call and sharing the selected metadata
                for this instruction.
              </label>
            </div>
          </>
        )}
        <textarea
          aria-label="Editing instruction"
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setConsent(false);
          }}
          placeholder={
            mode === "local"
              ? "Try: use camera B from 2 to 5 seconds"
              : "Describe your next cut…"
          }
          rows={4}
        />
        <Button
          className="wide accent"
          disabled={
            editor.busy ||
            !prompt.trim() ||
            !!planningJob ||
            (mode !== "local" &&
              (!consent ||
                !model ||
                !aiAssets.length ||
                aiAssets.length > 20 ||
                !status?.providers.find((s) => s.id === mode)?.configured ||
                (frames &&
                  (mode !== "openrouter" ||
                    !models
                      .find((m) => m.id === model)
                      ?.input_modalities.includes("image") ||
                    aiAssets.length < 1 ||
                    aiAssets.length > 3 ||
                    aiAssets.some(
                      (id) =>
                        p.assets[id]?.kind !== "video" ||
                        p.assets[id]?.status !== "ready",
                    )))))
          }
          onClick={submit}
        >
          Review proposal <ArrowRight size={15} />
        </Button>
        {plan && (
          <div className="proposal">
            <div className="proposal-heading">
              <strong>Proposed edit</strong>
              <Button onClick={() => setPlan(null)} title="Dismiss proposal">
                <X size={13} />
              </Button>
            </div>
            <p>{plan.summary ?? plan.label}</p>
            <p className="muted">
              Based on revision {plan.base_revision} ·{" "}
              {plan.operations?.length ?? 0} operations
            </p>
            <details>
              <summary>Review operations</summary>
              <pre>{JSON.stringify(plan.operations, null, 2)}</pre>
            </details>
            {!!plan.usage && (
              <details>
                <summary>Returned provider usage</summary>
                <pre>{JSON.stringify(plan.usage, null, 2)}</pre>
              </details>
            )}
            {plan.base_revision !== p.revision && (
              <p className="error-text">
                Timeline changed. Request a fresh proposal.
              </p>
            )}
            <Button
              className="wide accent"
              disabled={
                editor.busy ||
                plan.base_revision !== p.revision ||
                !plan.operations?.length
              }
              onClick={() =>
                void editor
                  .transact(
                    plan.label,
                    plan.operations,
                    plan.proposal_job_id ? "ai" : "manual",
                    plan.base_revision,
                    plan.proposal_job_id,
                  )
                  .then((result) => {
                    if (result) setPlan(null);
                  })
              }
            >
              <Check size={14} />{" "}
              {plan.operations?.length ? "Apply edit" : "Clarification needed"}
            </Button>
          </div>
        )}
        <details className="command-help">
          <summary>Supported offline commands</summary>
          <p>
            “use camera B from 2 to 5 seconds”
            <br />
            “keep audio from camera A”
            <br />
            “make a vertical version”
            <br />
            “make a 5-second highlight”
          </p>
          <p>
            This parser supports explicit commands, bounded local edits, with no
            semantic understanding.
          </p>
        </details>
      </Section>
      <Section title="Synchronization">
        {sync && (
          <p className={sync.status === "failed" ? "error-text" : "muted"}>
            {sync.error ?? sync.message}
          </p>
        )}
        {syncResult && (
          <div className="proposal">
            <strong>Measured alignment proposal</strong>
            {Object.entries(
              (syncResult.offsets ?? {}) as Record<string, number>,
            ).map(([id, value]) => (
              <div className="field" key={id}>
                <span>{p.assets[id]?.name}</span>
                <span className="mono">{seconds(value)} sec</span>
                <span>
                  {Math.round(
                    (((syncResult.confidence ?? {}) as Record<string, number>)[
                      id
                    ] ?? 0) * 100,
                  )}
                  %
                </span>
              </div>
            ))}
            {((syncResult.warnings ?? []) as string[]).map((w, i) => (
              <p className="error-text" key={i}>
                {w}
              </p>
            ))}
            <Button className="wide" disabled={editor.busy} onClick={applySync}>
              <Link2 size={14} /> Apply alignment
            </Button>
          </div>
        )}
        {p.sync.warnings.map((w, i) => (
          <p className="error-text" key={i}>
            {w}
          </p>
        ))}
        <label className="field">
          <span>Reference</span>
          <select
            aria-label="Sync reference"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          >
            {Object.values(p.assets).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          Offset = event time − source time. Source mappings stay stable after
          alignment.
        </p>
        {Object.values(p.assets).map((a) => (
          <Numeric
            key={a.id}
            label={`${a.name} offset (sec)`}
            value={offsets[a.id] ?? 0}
            onChange={(v) => setOffsets((o) => ({ ...o, [a.id]: v }))}
          />
        ))}
        <Button
          className="wide"
          disabled={!reference || editor.busy}
          onClick={() => manualSync()}
        >
          Apply manual offsets
        </Button>
        <details>
          <summary>Match manual anchor points</summary>
          <label className="field">
            <span>Matching camera</span>
            <select
              aria-label="Anchor camera"
              value={anchorAsset}
              onChange={(e) => setAnchorAsset(e.target.value)}
            >
              <option value="">Select camera</option>
              {Object.values(p.assets)
                .filter((a) => a.id !== reference)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
          <Numeric
            label="Reference moment (sec)"
            value={referenceSec}
            min={0}
            onChange={setReferenceSec}
          />
          <Numeric
            label="Matching source (sec)"
            value={anchorSec}
            min={0}
            onChange={setAnchorSec}
          />
          <Button
            disabled={!anchorAsset || editor.busy}
            onClick={() => {
              const offset =
                (offsets[reference] ?? 0) + referenceSec - anchorSec;
              setOffsets((o) => ({ ...o, [anchorAsset]: offset }));
              setAnchors((a) => [
                ...a,
                {
                  asset_id: anchorAsset,
                  source_us: microseconds(anchorSec),
                  event_us: microseconds(
                    (offsets[reference] ?? 0) + referenceSec,
                  ),
                },
              ]);
            }}
          >
            Calculate matching offset
          </Button>
          <p className="muted">
            Compare the same visible moment, calculate, then apply manual
            offsets. Constant offset only; drift correction is unavailable.
          </p>
        </details>
      </Section>
    </div>
  );
}
