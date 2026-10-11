import type { Operation, Project } from "./types";
let token: string | null = null;
async function session() {
  if (!token) {
    const r = await fetch("/api/session");
    if (!r.ok)
      throw new Error(
        "Local backend unavailable. Start AngleForge and reload.",
      );
    token = (await r.json()).token;
  }
  return token!;
}
export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const mutate = method ?? (body === undefined ? "GET" : "POST");
  const headers: Record<string, string> = {};
  if (mutate !== "GET") headers["X-AngleForge-Token"] = await session();
  if (body !== undefined && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const r = await fetch(`/api${path}`, {
    method: mutate,
    headers,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = `Request failed (${r.status})`;
    try {
      const result = await r.json();
      detail =
        typeof result.detail === "string"
          ? result.detail
          : JSON.stringify(result.detail);
    } catch {}
    throw new Error(detail);
  }
  return r.json();
}
export const edit = (
  p: Project,
  label: string,
  operations: Operation[],
  actor: "manual" | "ai" = "manual",
  proposalJobId?: string,
) =>
  api<Project>(`/projects/${p.id}/transactions`, {
    base_revision: p.revision,
    label,
    actor,
    operations,
    ...(proposalJobId ? { proposal_job_id: proposalJobId } : {}),
  });
export const media = (id: string, variant = "thumbnail") =>
  `/api/media/${id}/${variant}`;
