"use client";

import { useEffect, useState } from "react";

/**
 * The document's own table of contents, and where you are in it.
 *
 * A run used to be six tabs. Tabs answer "where can I go" and never "what is
 * there", so a person who did not already know the pipeline could not tell
 * which of them held the answer — and the six had to be visited in some order
 * they had to invent. One document has the opposite problem: it is long. This
 * is what makes it navigable, and it does the one thing tabs could not, which
 * is show the whole shape at once.
 *
 * Highlighting follows the scroll rather than the click, so it is a report of
 * where the reader is and not a memory of what they pressed.
 */
export default function Contents({ sections }: { sections: { id: string; label: string }[] }) {
  const [active, setActive] = useState(sections[0]?.id ?? "");

  useEffect(() => {
    setActive(sections[0]?.id ?? "");
  }, [sections]);

  useEffect(() => {
    const targets = sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => el !== null);
    if (targets.length === 0) return;

    // The top quarter of the viewport is "where the reader is looking". A
    // whole-viewport root margin makes every section active at once on a
    // short page, which reads as the highlight being broken.
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "0px 0px -75% 0px", threshold: 0 },
    );
    targets.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [sections]);

  return (
    <nav className="doc-toc" aria-label="Sections of this run">
      <ul>
        {sections.map((section) => (
          <li key={section.id}>
            <a href={`#${section.id}`} data-active={active === section.id ? "true" : undefined}>
              {section.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
