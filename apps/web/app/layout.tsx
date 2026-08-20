import type { ReactNode } from "react";
import "./globals.css";
import { THEME_BOOTSTRAP } from "@/lib/theme";

export const metadata = {
  title: "DeepAgents-scRNA — scientific runs",
  // Not "read-only" any more: with an analysis controller configured this app
  // can prepare and start a run and answer a human gate. The header says which
  // of the two a given deployment is; this line should not contradict it.
  description: "Observation and intake UI over recorded scRNA-seq runs.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `suppressHydrationWarning` on <html> only, and only for this reason:
    // browser extensions write attributes onto this element before React
    // hydrates — a translation extension adds
    // `data-immersive-translate-page-theme`, theme extensions add their own —
    // and React reports the mismatch as a hydration error on every page load.
    // Nothing this app renders can be affected: the flag covers attribute
    // differences on this one element and does not cascade to any child, so a
    // genuine hydration bug anywhere in the tree is still reported.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Runs before the first paint, so a dark-theme reader never sees a
            white frame on the way in. It only ever sets `data-theme`; the
            absence of that attribute is the "follow the system" state, and is
            what every page had before this existed. See `lib/theme.ts`. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
