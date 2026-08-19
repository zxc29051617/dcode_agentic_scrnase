"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import type { Data, Layout } from "plotly.js";
import type { ArtifactEntry } from "@/lib/gatewayTypes";

/** How tall the plot is, in one place, so the placeholder reserves exactly it. */
const PLOT_HEIGHT = 650;

/**
 * A placeholder that occupies the plot's own height while something is pending.
 *
 * The size is the point. Plotly draws into a box this tall, and a placeholder
 * that is any shorter makes the page jump when the real chart arrives — which
 * reads as a second bug on top of the wait.
 */
function PlotPending({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="embedding-loading"
      style={{
        height: `${PLOT_HEIGHT}px`,
        display: "grid",
        placeItems: "center",
        alignContent: "center",
        gap: "0.9rem",
        border: "1px dashed var(--line, #d5dae2)",
        borderRadius: "6px",
      }}
    >
      {/* A determinate-looking bar rather than a spinner: this wait is seconds
          long and a spinner that never fills reads as stuck. `prefers-reduced-
          motion` drops the animation and leaves a static bar, which still says
          "something belongs here" — the whole job of this element. */}
      <div
        aria-hidden="true"
        style={{
          width: "min(240px, 40%)",
          height: "3px",
          borderRadius: "999px",
          background: "var(--line, #d5dae2)",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <span className="embedding-pending-bar" />
      </div>
      <span className="subtle">{label}</span>
      <style>{`
        .embedding-pending-bar {
          position: absolute; inset: 0; display: block;
          background: currentColor; opacity: .55;
          transform-origin: left center;
          animation: embedding-pending 1.4s ease-in-out infinite;
        }
        @keyframes embedding-pending {
          0%   { transform: translateX(-100%) scaleX(.35); }
          50%  { transform: translateX(35%)  scaleX(.55); }
          100% { transform: translateX(190%) scaleX(.35); }
        }
        @media (prefers-reduced-motion: reduce) {
          .embedding-pending-bar { animation: none; transform: scaleX(.4); }
        }
      `}</style>
    </div>
  );
}

/**
 * Plotly, loaded in the browser only, with something to look at while it comes.
 *
 * `react-plotly.js` pulls in plotly.js, which is by far the largest thing this
 * app ships — seconds to fetch, parse and draw on a first visit. Without the
 * `loading` option `next/dynamic` renders *nothing* until it resolves, so the
 * page showed working controls above an empty 650px gap and no explanation.
 * Every person who saw that concluded the chart was broken, which is a fair
 * reading: a blank space says nothing, and a blank space where a chart clearly
 * belongs says something is wrong.
 *
 * Note this is a distinct wait from fetching the embedding JSON, which the
 * component already reported. Two waits happen in sequence and only the first
 * had a message; the visible gap was always the second one.
 */
const Plot = dynamic(() => import("react-plotly.js"), {
  ssr: false,
  loading: () => <PlotPending label="Loading the plotting library…" />,
});

type ColorColumn = {
  kind: "categorical" | "numeric";
  values: Array<string | number | null>;
};

type EmbeddingDocument = {
  basis: string;
  method: string;
  dimensions: 2 | 3;
  total_cells?: number;
  displayed_cells?: number;
  downsampled?: boolean;
  cells: string[];
  coordinates: number[][];
  colors: Record<string, ColorColumn>;
};

type Props = {
  runId: string;
  dataArtifacts: ArtifactEntry[];
  standaloneArtifacts: ArtifactEntry[];
};

function artifactUrl(runId: string, artifactId: string): string {
  return `/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(artifactId)}`;
}

function viewRank(name: string): number {
  const stem = name.replace(/^m3_/, "").replace(/\.json$/, "");
  return ["umap", "umap_3d", "tsne", "tsne_3d"].indexOf(stem);
}

function viewLabel(name: string): string {
  const stem = name.replace(/^m3_/, "").replace(/\.json$/, "");
  const dimensions = stem.endsWith("_3d") ? 3 : 2;
  const method = stem.replace(/_3d$/, "");
  const label = method === "tsne" ? "t-SNE" : method.toUpperCase();
  return `${label} ${dimensions}D`;
}

function validateDocument(value: unknown, artifactName: string): EmbeddingDocument {
  if (!value || typeof value !== "object") throw new Error(`${artifactName}: invalid JSON object`);
  const document = value as Partial<EmbeddingDocument>;
  if (document.dimensions !== 2 && document.dimensions !== 3) {
    throw new Error(`${artifactName}: dimensions must be 2 or 3`);
  }
  if (!Array.isArray(document.cells) || !Array.isArray(document.coordinates)) {
    throw new Error(`${artifactName}: cells and coordinates are required`);
  }
  if (document.cells.length !== document.coordinates.length) {
    throw new Error(`${artifactName}: cells and coordinates have different lengths`);
  }
  if (document.coordinates.some((row) => !Array.isArray(row) || row.length !== document.dimensions)) {
    throw new Error(`${artifactName}: coordinate shape does not match dimensions`);
  }
  if (!document.colors || typeof document.colors !== "object") {
    throw new Error(`${artifactName}: colors are missing`);
  }
  for (const [name, column] of Object.entries(document.colors)) {
    if (!column || !Array.isArray(column.values) || column.values.length !== document.cells.length) {
      throw new Error(`${artifactName}: color column ${name} has the wrong length`);
    }
  }
  return document as EmbeddingDocument;
}

function uniqueValues(values: Array<string | number | null>): string[] {
  return Array.from(new Set(values.filter((value): value is string | number => value !== null).map(String)));
}

function tracesFor(document: EmbeddingDocument, colorName: string): Data[] {
  const coordinates = document.coordinates;
  const color = document.colors[colorName];
  const common = {
    mode: "markers" as const,
    text: document.cells,
    hovertemplate: "%{text}<extra></extra>",
    marker: { size: 6 },
  };

  if (!colorName || !color) {
    return [{
      ...common,
      type: document.dimensions === 3 ? "scatter3d" : "scattergl",
      x: coordinates.map((row) => row[0]),
      y: coordinates.map((row) => row[1]),
      ...(document.dimensions === 3 ? { z: coordinates.map((row) => row[2]) } : {}),
      name: "cells",
    } as Data];
  }

  if (color.kind === "numeric") {
    return [{
      ...common,
      type: document.dimensions === 3 ? "scatter3d" : "scattergl",
      x: coordinates.map((row) => row[0]),
      y: coordinates.map((row) => row[1]),
      ...(document.dimensions === 3 ? { z: coordinates.map((row) => row[2]) } : {}),
      name: colorName,
      marker: {
        size: 6,
        color: color.values.map((value) => value === null ? Number.NaN : Number(value)),
        colorscale: "Viridis",
        showscale: true,
        colorbar: { title: colorName },
      },
    } as Data];
  }

  const categories = uniqueValues(color.values);
  return categories.map((category) => {
    const selected = coordinates
      .map((row, index) => ({ row, index }))
      .filter(({ index }) => String(color.values[index]) === category);
    return {
      ...common,
      type: document.dimensions === 3 ? "scatter3d" : "scattergl",
      x: selected.map(({ row }) => row[0]),
      y: selected.map(({ row }) => row[1]),
      ...(document.dimensions === 3 ? { z: selected.map(({ row }) => row[2]) } : {}),
      text: selected.map(({ index }) => document.cells[index]),
      name: category,
    } as Data;
  });
}

export default function EmbeddingViewer({ runId, dataArtifacts, standaloneArtifacts }: Props) {
  const availableArtifacts = useMemo(
    () => dataArtifacts.filter((artifact) => !artifact.too_large).sort(
      (a, b) => viewRank(a.name) - viewRank(b.name) || a.name.localeCompare(b.name),
    ),
    [dataArtifacts],
  );
  const [documents, setDocuments] = useState<Record<string, EmbeddingDocument>>({});
  const [selectedArtifactId, setSelectedArtifactId] = useState("");
  const [colorName, setColorName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (availableArtifacts.length === 0) {
      setSelectedArtifactId("");
      return;
    }
    setSelectedArtifactId((previous) => (
      availableArtifacts.some((artifact) => artifact.artifact_id === previous)
        ? previous
        : availableArtifacts[0].artifact_id
    ));
  }, [availableArtifacts]);

  const selectedArtifact = availableArtifacts.find(
    (artifact) => artifact.artifact_id === selectedArtifactId,
  );
  const document = selectedArtifact ? documents[selectedArtifact.artifact_id] : undefined;

  useEffect(() => {
    if (!selectedArtifact || documents[selectedArtifact.artifact_id]) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(artifactUrl(runId, selectedArtifact.artifact_id), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`${selectedArtifact.name}: HTTP ${response.status}`);
        return validateDocument(await response.json(), selectedArtifact.name);
      })
      .then((loaded) => {
        setDocuments((previous) => ({ ...previous, [selectedArtifact.artifact_id]: loaded }));
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause.message : "Could not load embedding data");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [documents, runId, selectedArtifact]);

  const colorOptions = document ? Object.keys(document.colors) : [];
  const activeColor = colorOptions.includes(colorName) ? colorName : "";
  const traces = useMemo(
    () => document ? tracesFor(document, activeColor) : [],
    [document, activeColor],
  );
  const layout = useMemo<Partial<Layout>>(() => ({
    title: document ? { text: `${document.method.toUpperCase()} ${document.dimensions}D` } : undefined,
    height: PLOT_HEIGHT,
    autosize: true,
    margin: { l: 20, r: 20, t: 55, b: 20 },
    legend: { orientation: "h" },
  }), [document]);

  if (dataArtifacts.length === 0) {
    return <p style={{ margin: 0 }}>No interactive embedding data was recorded for this run.</p>;
  }
  if (availableArtifacts.length === 0) {
    return <p style={{ margin: 0 }}>All embedding views are larger than the gateway display limit.</p>;
  }
  if (error) {
    return <p data-tone="warn" style={{ margin: 0 }}>Could not load embedding data: {error}</p>;
  }
  // The first visit has no document yet, so there are no controls to keep and
  // the placeholder stands alone. Reserving the plot's height here too means
  // the section does not resize twice on the way to showing a chart.
  //
  // The label is unconditional because by this point a view is always on its
  // way: the guards above have established that there is at least one usable
  // artifact, and an effect selects the first one. Saying "select a view to
  // begin" — as this did — described a state the component cannot be in, and
  // it showed during the very first render, before the effect had run, so the
  // one message a person actually saw was the one that was never true.
  if (!document || !selectedArtifact) {
    return <PlotPending label="Loading embedding data…" />;
  }

  const totalCells = document.total_cells ?? document.cells.length;
  const displayedCells = document.displayed_cells ?? document.cells.length;

  return (
    <div>
      <div className="controls">
        <label>
          View{" "}
          <select value={selectedArtifact.artifact_id} onChange={(event) => setSelectedArtifactId(event.target.value)}>
            {availableArtifacts.map((artifact) => (
              <option key={artifact.artifact_id} value={artifact.artifact_id}>
                {viewLabel(artifact.name)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Color{" "}
          <select value={activeColor} onChange={(event) => setColorName(event.target.value)}>
            <option value="">None</option>
            {colorOptions.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        <span className="subtle">
          {displayedCells.toLocaleString()} of {totalCells.toLocaleString()} cells
          {document.downsampled ? " (display subset)" : ""}
        </span>
        {/* Switching to a view whose data is not cached fetches again. The
            chart below stays on screen showing the *previous* view while that
            happens, so without this the controls say one thing and the plot
            shows another with nothing to explain the gap. */}
        {loading && (
          <span className="subtle" role="status" aria-live="polite" data-testid="embedding-switching">
            loading the selected view…
          </span>
        )}
      </div>
      {dataArtifacts.length > availableArtifacts.length && (
        <p className="subtle" style={{ margin: "0 0 0.5rem" }}>
          Some views exceed the gateway display limit and are unavailable in the browser.
        </p>
      )}
      <Plot
        data={traces}
        layout={layout}
        config={{ responsive: true, displaylogo: false }}
        useResizeHandler
        style={{ width: "100%", height: "650px" }}
      />
      {standaloneArtifacts.length > 0 && (
        <p className="subtle" style={{ marginTop: 0 }}>
          Standalone Plotly files are available from the Artifacts page.
        </p>
      )}
    </div>
  );
}
