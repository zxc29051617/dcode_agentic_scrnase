import type { ComponentPropsWithoutRef, ReactNode } from "react";
import type { Tone } from "@/lib/verdict";

export default function Badge({
  tone,
  children,
  ...props
}: ComponentPropsWithoutRef<"span"> & { tone: Tone; children: ReactNode }) {
  return (
    <span className="badge" data-tone={tone} {...props}>
      {children}
    </span>
  );
}
