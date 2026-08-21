"use client";

import type { ArtifactEntry } from "@/lib/gatewayTypes";

/**
 * Report figures, shown as images.
 *
 * Each `src` is the same-origin artifact route, so the browser never learns
 * the gateway's address, and the id in it is one the manifest produced — a
 * figure this run did not publish has no id and therefore cannot be
 * requested.
 */
export default function FigureGallery({
  runId,
  figures,
  emptyReason,
}: {
  runId: string;
  figures: ArtifactEntry[];
  emptyReason: string;
}) {
  if (figures.length === 0) {
    return (
      <p className="subtle" style={{ margin: 0 }}>
        {emptyReason}
      </p>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
        gap: "1rem",
      }}
    >
      {figures.map((figure) => {
        const href = `/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(figure.artifact_id)}`;
        return (
          <figure key={figure.artifact_id} style={{ margin: 0 }}>
            <a href={href} target="_blank" rel="noopener noreferrer" title="open full size">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={href}
                alt={figure.name}
                loading="lazy"
                style={{
                  width: "100%",
                  height: "auto",
                  border: "1px solid var(--line)",
                  borderRadius: "8px",
                  // Stays light in both themes, on purpose — see `--foreign-bg`
                  // in globals.css. A matplotlib PNG with a transparent
                  // background and black axis labels is unreadable on a dark
                  // panel, so this figure keeps the page it was drawn for.
                  background: "var(--foreign-bg)",
                }}
              />
            </a>
            <figcaption className="subtle" style={{ marginTop: "0.3rem" }}>
              <code>{figure.name}</code>
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}
