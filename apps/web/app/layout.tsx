import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "DeepAgents-scRNA — scientific runs",
  description: "Read-only observation UI over recorded scRNA-seq runs.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
