import { formatCount } from "@/lib/verdict";

/**
 * A number the run recorded, or a stated absence.
 *
 * `null` renders as "not recorded" rather than 0 or a dash with no
 * explanation. A run kept by copying it into `results/` may have had most of
 * its per-step `output.json` files dropped, and showing 0 cells for such a
 * run would be a fabricated result, not a formatting choice.
 *
 * Where each number came from is shown in a real tooltip rather than a `title`
 * attribute. That distinction is not cosmetic: a native tooltip waits about a
 * second, cannot wrap or be styled, and never appears at all on a touch
 * screen — so "from annotate_cells" was, for a large share of readers, simply
 * absent. It is the sort of thing that looks present in code review and is not
 * present on the page. See `.tip` in `globals.css`.
 */
export default function SummaryCards({
  items,
}: {
  items: { label: string; value: number | string | null; title?: string }[];
}) {
  return (
    <div className="cards">
      {items.map((item, index) => {
        const text =
          typeof item.value === "number" ? formatCount(item.value) : (item.value ?? null);
        return (
          <div className="card" key={item.label}>
            {item.title ? (
              <div
                className="card-label tip"
                tabIndex={0}
                // The first card in a row would have a centred tooltip clipped
                // by the panel edge, so it hangs from the left instead.
                data-align={index === 0 ? "start" : undefined}
              >
                {item.label}
                <span className="tip-text" role="tooltip">
                  {item.title}
                </span>
              </div>
            ) : (
              <div className="card-label">{item.label}</div>
            )}
            <div className="card-value" data-empty={text === null ? "true" : undefined}>
              {text ?? "not recorded"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
