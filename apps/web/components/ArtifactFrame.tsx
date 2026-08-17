"use client";

import { useState } from "react";
import type { ArtifactEntry } from "@/lib/gatewayTypes";

/**
 * A third-party HTML report, shown inside a sandbox.
 *
 * `sandbox="allow-scripts"` and **no** `allow-same-origin`. Those two
 * together would be no sandbox at all — the framed document could reach back
 * into this app's origin and remove its own sandbox attribute. With scripts
 * but an opaque origin, MultiQC's plots and Cell Ranger's charts still work
 * and neither can read a cookie, a token or anything else of ours.
 *
 * The report is never parsed or inlined. There is no `dangerouslySetInnerHTML`
 * anywhere in this app: the bytes go to the browser as a separate document
 * over `/api/artifacts/...`, which is the whole reason the sandbox can apply
 * to them at all.
 *
 * Loaded on demand. A MultiQC report is tens of megabytes, and fetching one
 * for a tab nobody opened is a slow page for no reason.
 */
export default function ArtifactFrame({
  runId,
  artifact,
  height = "70vh",
}: {
  runId: string;
  artifact: ArtifactEntry;
  height?: string;
}) {
  const [open, setOpen] = useState(false);
  const src = `/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(artifact.artifact_id)}`;

  if (artifact.too_large) {
    return (
      <div className="panel" data-tone="warn">
        <p style={{ margin: 0 }}>
          <code>{artifact.name}</code> is {(artifact.size_bytes / 1_048_576).toFixed(1)} MB, over
          the size this gateway will serve. Download it and open it locally.
        </p>
      </div>
    );
  }

  return (
    <div style={{ marginBottom: "1.25rem" }}>
      <div className="controls">
        <strong>{artifact.name}</strong>
        <span className="subtle">{(artifact.size_bytes / 1024).toFixed(0)} KB</span>
        <span className="spacer" />
        <button onClick={() => setOpen((v) => !v)} data-variant={open ? undefined : "primary"}>
          {open ? "Hide" : "Show report"}
        </button>
        <a className="nav-item" href={src} target="_blank" rel="noopener noreferrer">
          Open isolated report ↗
        </a>
        <a className="nav-item" href={`${src}?download=1`}>
          Download
        </a>
      </div>

      {open && (
        <iframe
          src={src}
          title={artifact.name}
          // No allow-same-origin, deliberately. See the note above.
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          style={{
            width: "100%",
            height,
            border: "1px solid var(--line)",
            borderRadius: "8px",
            background: "#fff",
          }}
        />
      )}
    </div>
  );
}
