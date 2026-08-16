"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import dynamic from "next/dynamic";
import Badge from "@/components/Badge";
import { runTone } from "@/lib/verdict";

/**
 * The assistant is loaded only when somebody opens it.
 *
 * `@copilotkit/react-ui` is 755 kB of the 867 kB the assistant route weighs;
 * the run list is 112 kB without it. Importing it in a shell that wraps every
 * page would put that on the first paint of `/runs`, which is the one page
 * that must stay quick. `ssr: false` also keeps it out of the server render,
 * so a page still renders if the assistant bundle fails to load at all.
 */
const AssistantPanel = dynamic(() => import("@/components/AssistantPanel"), {
  ssr: false,
  loading: () => <p className="subtle" style={{ padding: "1rem" }}>Loading assistant…</p>,
});

export type ShellRun = {
  id: string;
  status: string;
  hasReport: boolean;
} | null;

export default function AppShell({
  run,
  assistantConfigured,
  assistantReason,
  assistantModel,
  assistantEndpoint,
  instructions,
  children,
}: {
  run: ShellRun;
  assistantConfigured: boolean;
  assistantReason: string | null;
  /** Model name, shown so it is visible which model would answer. Never the key. */
  assistantModel: string | null;
  /** Endpoint with credentials already stripped by `describeAssistantModel`. */
  assistantEndpoint: string | null;
  instructions: string;
  children: React.ReactNode;
}) {
  const [asideOpen, setAsideOpen] = useState(false);
  const pathname = usePathname();

  const base = run ? `/runs/${encodeURIComponent(run.id)}` : null;
  const links: { href: string; label: string; enabled: boolean }[] = base
    ? [
        { href: base, label: "Overview", enabled: true },
        { href: `${base}/workflow`, label: "Workflow", enabled: true },
        { href: `${base}/report`, label: "Report", enabled: run!.hasReport },
        { href: `${base}/provenance`, label: "Provenance", enabled: true },
      ]
    : [];

  return (
    <div className="shell" data-aside={asideOpen ? "open" : "closed"}>
      <div className="shell-brand">DeepAgents&#8209;scRNA</div>

      <header className="shell-top">
        {run ? (
          <>
            <strong style={{ fontFamily: "ui-monospace, Menlo, monospace" }}>{run.id}</strong>
            <Badge tone={runTone(run.status)}>{run.status}</Badge>
          </>
        ) : (
          <strong>Scientific runs</strong>
        )}
        <span className="spacer" />
        {/* Which model would answer, rendered server-side and always visible —
            so it is knowable without opening the chat, and so a run recorded
            from one endpoint is not silently discussed by another. The key is
            never here; `describeAssistantModel` strips credentials from the
            endpoint before it reaches this component. */}
        {assistantConfigured && assistantModel ? (
          <span className="subtle" title={assistantEndpoint ?? undefined}>
            model <code>{assistantModel}</code>
          </span>
        ) : (
          // Server-rendered, so the absence is visible before anyone clicks.
          // Discovering that chat does not work only after opening it is the
          // thing the unconfigured branch exists to prevent.
          <span className="badge" data-tone="muted" title={assistantReason ?? undefined}>
            Assistant model is not configured
          </span>
        )}
        <span className="subtle">read-only</span>
        <button
          onClick={() => setAsideOpen((open) => !open)}
          data-variant={asideOpen || !assistantConfigured ? undefined : "primary"}
          aria-expanded={asideOpen}
        >
          {asideOpen ? "Close assistant" : "Assistant"}
        </button>
      </header>

      <nav className="shell-nav">
        <div className="nav-group">
          <div className="nav-label">Browse</div>
          <Link className="nav-item" href="/runs" aria-current={pathname === "/runs" ? "page" : undefined}>
            Runs
          </Link>
        </div>
        {run && (
          <div className="nav-group">
            <div className="nav-label">This run</div>
            {links.map((link) => (
              <Link
                key={link.href}
                className="nav-item"
                href={link.enabled ? link.href : "#"}
                data-disabled={link.enabled ? undefined : "true"}
                aria-current={pathname === link.href ? "page" : undefined}
                title={link.enabled ? undefined : "no report was produced for this run"}
              >
                {link.label}
              </Link>
            ))}
          </div>
        )}
      </nav>

      <main className="shell-main">{children}</main>

      {asideOpen && (
        <aside className="shell-aside" aria-label="AI assistant">
          <div className="aside-head">
            <strong>Assistant</strong>
            <span className="spacer" />
            <button onClick={() => setAsideOpen(false)} aria-label="Close assistant">
              ×
            </button>
          </div>
          <div className="aside-body">
            {assistantConfigured ? (
              <AssistantPanel runId={run?.id ?? null} instructions={instructions} />
            ) : (
              <div style={{ padding: "1rem" }}>
                <p>
                  <strong>Assistant model is not configured.</strong>
                </p>
                <p className="subtle">{assistantReason}</p>
                <p className="subtle">
                  Set <code>ASSISTANT_MODEL_BASE_URL</code> and <code>ASSISTANT_MODEL_NAME</code> in{" "}
                  <code>apps/web/.env.local</code>, then restart. Every other page works without a
                  model.
                </p>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
