import { useCallback, useEffect, useRef, useState } from "react";
import { api, edit } from "./api";
import type { Job, Operation, Project, ProjectSummary } from "./types";
export function useEditor() {
  const [project, setProject] = useState<Project | null>(null),
    [projects, setProjects] = useState<ProjectSummary[]>([]),
    [jobs, setJobs] = useState<Job[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState("Saved locally");
  const current = useRef<Project | null>(null);
  current.current = project;
  const run = useCallback(
    async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
      setBusy(true);
      setError("");
      try {
        return await fn();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [],
  );
  const refreshList = useCallback(async () => {
    setProjects(
      (await api<{ projects: ProjectSummary[] }>("/projects")).projects,
    );
  }, []);
  const open = useCallback(
    async (id: string) => {
      await run(async () => {
        const p = await api<Project>(`/projects/${id}`);
        setProject(p);
        localStorage.setItem("angleforge-project", id);
        setSaved("Saved locally");
      });
    },
    [run],
  );
  useEffect(() => {
    void run(async () => {
      const list = await api<{ projects: ProjectSummary[] }>("/projects");
      setProjects(list.projects);
      const stored = localStorage.getItem("angleforge-project");
      const id =
        list.projects.find((p) => p.id === stored)?.id ?? list.projects[0]?.id;
      if (id) {
        setProject(await api<Project>(`/projects/${id}`));
      } else {
        const p = await api<Project>("/projects", {
          name: "Untitled multicam",
        });
        setProject(p);
        localStorage.setItem("angleforge-project", p.id);
        setProjects([
          { id: p.id, name: p.name, revision: p.revision, updated_at: "" },
        ]);
      }
    });
  }, [run]);
  useEffect(() => {
    let canceled = false;
    const poll = async () => {
      try {
        const next = (await api<{ jobs: Job[] }>("/jobs")).jobs;
        if (canceled) return;
        setJobs(next);
        const p = current.current;
        if (p) {
          const fresh = await api<Project>(`/projects/${p.id}`);
          if (
            !canceled &&
            current.current?.id === fresh.id &&
            current.current.revision !== fresh.revision
          )
            setProject(fresh);
        }
      } catch {}
    };
    void poll();
    const id = setInterval(poll, 1000);
    return () => {
      canceled = true;
      clearInterval(id);
    };
  }, []);
  const transact = useCallback(
    async (
      label: string,
      operations: Operation[],
      actor: "manual" | "ai" = "manual",
      baseRevision?: number,
      proposalJobId?: string,
    ) => {
      const p = current.current;
      if (!p) return;
      return run(async () => {
        const updated = await edit(
          baseRevision === undefined ? p : { ...p, revision: baseRevision },
          label,
          operations,
          actor,
          proposalJobId,
        );
        setProject(updated);
        setSaved("Saved locally");
        return updated;
      });
    },
    [run],
  );
  const history = useCallback(
    async (kind: "undo" | "redo") => {
      const p = current.current;
      if (p)
        await run(async () => {
          setProject(
            await api<Project>(`/projects/${p.id}/${kind}`, {
              base_revision: p.revision,
            }),
          );
        });
    },
    [run],
  );
  const save = useCallback(async () => {
    const p = current.current;
    if (p)
      await run(async () => {
        await api(`/projects/${p.id}/save`, {});
        setSaved("Saved locally");
        await refreshList();
      });
  }, [run, refreshList]);
  const create = useCallback(async () => {
    await run(async () => {
      const p = await api<Project>("/projects", { name: "Untitled multicam" });
      setProject(p);
      localStorage.setItem("angleforge-project", p.id);
      await refreshList();
    });
  }, [run, refreshList]);
  const demo = useCallback(async () => {
    await run(async () => {
      const r = await api<{ project: Project; job: Job }>("/demo", {});
      setProject(r.project);
      localStorage.setItem("angleforge-project", r.project.id);
      setJobs((j) => [r.job, ...j]);
      await refreshList();
    });
  }, [run, refreshList]);
  const importFiles = useCallback(
    async (files: File[]) => {
      const p = current.current;
      if (!p || !files.length) return;
      await run(async () => {
        const body = new FormData();
        files.forEach((f) => body.append("files", f));
        const r = await api<{ jobs: Job[] }>(`/projects/${p.id}/import`, body);
        setJobs((j) => [...r.jobs, ...j]);
      });
    },
    [run],
  );
  return {
    project,
    projects,
    jobs,
    error,
    setError,
    busy,
    saved,
    run,
    open,
    transact,
    history,
    save,
    create,
    demo,
    importFiles,
  };
}
export type Editor = ReturnType<typeof useEditor>;
