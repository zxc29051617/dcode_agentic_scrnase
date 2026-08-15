import type { ReactNode } from "react";

export const metadata = {
  title: "scRNA-seq scientific runs — read-only",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: "2rem" }}>
        <p style={{ color: "#666", fontSize: "0.85rem" }}>
          Read-only observation UI. Nothing on this site starts a run, resumes a
          run, or answers a human gate.
        </p>
        {children}
      </body>
    </html>
  );
}
