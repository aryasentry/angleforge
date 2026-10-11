import {
  X,
  Download,
  LoaderCircle,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { api } from "../api";
import type { Editor } from "../useEditor";
import { IconButton } from "./Primitives";
export function Jobs({ editor }: { editor: Editor }) {
  const jobs = editor.jobs.slice(0, 6);
  return (
    <div className="jobs-list">
      {jobs.map((job) => (
        <div className={`job ${job.status}`} key={job.id}>
          <div className="job-heading">
            {job.status === "running" || job.status === "queued" ? (
              <LoaderCircle size={14} className="spin" />
            ) : job.status === "succeeded" ? (
              <CheckCircle2 size={14} />
            ) : (
              <AlertCircle size={14} />
            )}
            <strong>{job.kind}</strong>
            <span>{job.status}</span>
            {(job.status === "running" || job.status === "queued") && (
              <IconButton
                label={`Cancel ${job.kind}`}
                onClick={() =>
                  void editor.run(() => api(`/jobs/${job.id}/cancel`, {}))
                }
              >
                <X size={13} />
              </IconButton>
            )}
          </div>
          {job.progress !== null && job.status === "running" && (
            <progress max={1} value={job.progress} />
          )}
          <p>{job.error ?? job.message}</p>
          {job.status === "succeeded" &&
            typeof job.result?.download_url === "string" && (
              <a className="download-link" href={job.result.download_url}>
                <Download size={13} /> Download verified MP4
              </a>
            )}
        </div>
      ))}
    </div>
  );
}
