"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * The saved report, rendered — headings, tables and all — with a contents
 * list built from its own headings.
 *
 * Figures are the one thing that cannot render yet. `build_report` writes
 * them next to the report as relative paths (`figures/m1_funnel.png`), and
 * the gateway serves no artifact bytes, so an `<img>` would resolve against
 * this app's origin and break. A stated placeholder is the same discipline
 * `docs/report_contract.md` applies to the report itself: an absent figure
 * with a reason is evidence, an absent figure with no explanation is
 * indistinguishable from an oversight.
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
}: {
  content: string;
  sourcePath: string | null;
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
            img: ({ alt, src }) => (
              <span className="figure-missing">
                Figure not available in this view: <strong>{alt || String(src)}</strong> — the
                gateway serves no artifact files yet.
              </span>
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </article>
    </div>
  );
}
