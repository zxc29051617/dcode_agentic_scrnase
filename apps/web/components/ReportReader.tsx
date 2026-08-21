import { useMemo, type ComponentPropsWithoutRef, type ReactNode } from "react";
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

function textFromNode(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textFromNode).join("");
  if (node && typeof node === "object" && "props" in node) {
    const children = (node as { props?: { children?: ReactNode } }).props?.children;
    return textFromNode(children ?? null);
  }
  return "";
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
    const seen = new Map<string, number>();
    for (const line of content.split("\n")) {
      const match = /^(#{2,3})\s+(.*)$/.exec(line);
      if (!match) continue;
      const text = match[2].trim();
      const base = slug(text);
      const count = (seen.get(base) ?? 0) + 1;
      seen.set(base, count);
      found.push({ level: match[1].length, text, id: count === 1 ? base : `${base}-${count}` });
    }
    return found;
  }, [content]);

  let headingIndex = 0;
  const headingComponent = (tag: "h2" | "h3") => {
    const Tag = tag;
    return ({ children, ...props }: ComponentPropsWithoutRef<"h2">) => {
      const heading = headings[headingIndex++] ?? null;
      const text = textFromNode(children);
      return (
        <Tag {...props} id={heading?.id ?? slug(text)}>
          {children}
        </Tag>
      );
    };
  };

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
            h2: headingComponent("h2"),
            h3: headingComponent("h3"),
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
