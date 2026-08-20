"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import dynamic from "next/dynamic";
import Badge from "@/components/Badge";
import ThemeToggle from "@/components/ThemeToggle";
import { runTone } from "@/lib/verdict";

/** Where this browser's choice about the assistant panel is remembered. */
const ASIDE_PREFERENCE_KEY = "scrna.assistant.aside";

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
  assistantModelOrigin = null,
  instructions,
  canStartAnalyses = false,
  children,
}: {
  run: ShellRun;
  assistantConfigured: boolean;
  assistantReason: string | null;
  /** Model name, shown so it is visible which model would answer. Never the key. */
  assistantModel: string | null;
  /** Endpoint with credentials already stripped by `describeAssistantModel`. */
  assistantEndpoint: string | null;
  /**
   * Why this model and not the server's default — `null` when it *is* the
   * default. Shown beside the name so a visitor who supplied their own key can
   * see that it took, which the header previously gave no sign of at all.
   */
  assistantModelOrigin?: string | null;
  instructions: string;
  /**
   * Whether an analysis controller is configured.
   *
   * The header used to say "read-only" unconditionally, and that was true of
   * the whole app. It is no longer: with a controller, `/analysis/new` can
   * start a run and a run page can answer a gate. The label now says which of
   * the two this deployment is, because a stale "read-only" on a site that can
   * start an analysis is worse than no label at all.
   */
  canStartAnalyses?: boolean;
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  /**
   * The assistant panel starts open on a run page and closed on the run list,
   * and whatever a person chooses afterwards wins on every page from then on.
   *
   * The split keeps the bundle decision above intact rather than discarding
   * it. Opening the panel is what pulls 755 kB of `@copilotkit/react-ui`, and
   * `/runs` is the page that must stay quick — it is the first thing anyone
   * loads and nobody arrives there to ask a question. A run page is the
   * opposite: the assistant is there to explain the evidence on screen, and
   * having to click for it every time was the friction this removes.
   *
   * Read in an effect rather than during render because `localStorage` does
   * not exist on the server. Reading it in `useState`'s initialiser would make
   * the server and the client disagree about the very first frame, which is
   * exactly the hydration error this app has already been bitten by once.
   */
  const [asideOpen, setAsideOpen] = useState(false);
  useEffect(() => {
    const stored = window.localStorage.getItem(ASIDE_PREFERENCE_KEY);
    setAsideOpen(stored === null ? Boolean(run) : stored === "open");
    // Mount only. Re-running on navigation would overrule a person who closed
    // the panel on the previous page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleAside = useCallback(() => {
    setAsideOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem(ASIDE_PREFERENCE_KEY, next ? "open" : "closed");
      } catch {
        // Private browsing and full quotas both throw here. The panel still
        // opens; only the memory of it is lost, which is not worth failing on.
      }
      return next;
    });
  }, []);

  // Anchors into one document, not six pages. The labels are what the
  // sections are called on the page, so the rail there and this list agree —
  // two names for one section is how a reader ends up believing there are two.
  //
  // The in-page contents rail is the primary way to move around a run; this
  // stays because the shell is where somebody looks when they arrive from
  // elsewhere, and because it is the only navigation visible before the
  // document has finished rendering.
  const base = run ? `/runs/${encodeURIComponent(run.id)}` : null;
  const links: { href: string; label: string; enabled: boolean }[] = base
    ? [
        { href: `${base}#findings`, label: "What it found", enabled: true },
        { href: `${base}#report`, label: "Report", enabled: run!.hasReport },
        { href: `${base}#quality`, label: "Quality control", enabled: true },
        { href: `${base}#how`, label: "How it ran", enabled: true },
        { href: `${base}#provenance`, label: "Provenance", enabled: true },
        { href: `${base}#files`, label: "Files", enabled: true },
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
          <span
            className="subtle"
            title={
              assistantModelOrigin
                ? `${assistantModelOrigin} · ${assistantEndpoint ?? ""}`
                : (assistantEndpoint ?? undefined)
            }
            data-testid="assistant-model-label"
          >
            model <code>{assistantModel}</code>
            {assistantModelOrigin && (
              <Badge tone="reused">{assistantModelOrigin}</Badge>
            )}
          </span>
        ) : (
          // Server-rendered, so the absence is visible before anyone clicks.
          // Discovering that chat does not work only after opening it is the
          // thing the unconfigured branch exists to prevent.
          <span className="badge" data-tone="muted" title={assistantReason ?? undefined}>
            Assistant model is not configured
          </span>
        )}
        <span className="subtle" title={
          canStartAnalyses
            ? "an analysis controller is configured: this site can prepare and start runs, and answer human gates"
            : "no analysis controller is configured: every page here only reads recorded runs"
        }>
          {canStartAnalyses ? "read + start" : "read-only"}
        </span>
        <ThemeToggle />
        <button
          onClick={toggleAside}
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
          {canStartAnalyses && (
            <Link
              className="nav-item"
              href="/analysis/new"
              aria-current={pathname === "/analysis/new" ? "page" : undefined}
            >
              New analysis
            </Link>
          )}
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
            <button onClick={toggleAside} aria-label="Close assistant">
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
