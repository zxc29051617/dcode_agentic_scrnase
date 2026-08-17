"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * The saved report, rendered — headings, tables and all — with a contents
 * list built from its own headings.
 *
 * Figures are resolved from the run's artifact manifest rather than from
 * arbitrary Markdown URLs. `build_report` writes them next to the report as
 * relative paths (`figures/m1_funnel.png`), and this component maps only names
 * the gateway published to opaque artifact ids. Unknown figures remain an
 * explicit placeholder instead of making the browser fetch an arbitrary URL.
 */

type Heading = { level: number; text: string; id: string };

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s·-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

export default function ReportReader({
  content,
  sourcePath,
  runId,
  figures,
  embeddings,
}: {
  content: string;
  sourcePath: string | null;
  runId: string;
  figures: Record<string, string>;
  embeddings: Record<string, string>;
}) {
  const headings = useMemo<Heading[]>(() => {
    const found: Heading[] = [];
    for (const line of content.split("\n")) {
      const match = /^(#{2,3})\s+(.*)$/.exec(line);
      if (match) {
        const text = match[2].trim();
        found.push({ level: match[1].length, text, id: slug(text) });
      }
    }
    return found;
  }, [content]);

  return (
    <div className="report-layout">
      <nav className="report-toc" aria-label="Report contents">
        <div className="nav-label">Contents</div>
        {headings.map((h, i) => (
          <a key={`${h.id}-${i}`} href={`#${h.id}`} data-level={h.level}>
            {h.text}
          </a>
        ))}
        {sourcePath && (
          <p className="subtle" style={{ marginTop: "1rem" }}>
            read from <code>{sourcePath}</code>
          </p>
        )}
      </nav>

      <article className="report-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h2: ({ children }) => <h2 id={slug(String(children))}>{children}</h2>,
            h3: ({ children }) => <h3 id={slug(String(children))}>{children}</h3>,
            img: ({ alt, src }) => {
              const name = String(src ?? "").split("/").pop() ?? "";
              const id = figures[name];
              if (!id) {
                return (
                  <span className="figure-missing">
                    Figure not available: <strong>{alt || name || "unnamed"}</strong> — this run
                    published no artifact by that name.
                  </span>
                );
              }
              return (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(id)}`}
                  alt={alt || name}
                  loading="lazy"
                />
              );
            },
            a: ({ children, href }) => {
              const name = String(href ?? "").split("/").pop() ?? "";
              const id = embeddings[name];
              if (!id) return <a href={href}>{children}</a>;
              return (
                <a
                  href={`/api/artifacts/${encodeURIComponent(runId)}/${encodeURIComponent(id)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {children}
                </a>
              );
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </article>
    </div>
  );
}
