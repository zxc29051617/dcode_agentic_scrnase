import { formatCount } from "@/lib/verdict";

/**
 * A number the run recorded, or a stated absence.
 *
 * `null` renders as "not recorded" rather than 0 or a dash with no
 * explanation. A run kept by copying it into `results/` may have had most of
 * its per-step `output.json` files dropped, and showing 0 cells for such a
 * run would be a fabricated result, not a formatting choice.
 */
export default function SummaryCards({
  items,
}: {
  items: { label: string; value: number | string | null; title?: string }[];
}) {
  return (
    <div className="cards">
      {items.map((item) => {
        const text =
          typeof item.value === "number" ? formatCount(item.value) : (item.value ?? null);
        return (
          <div className="card" key={item.label} title={item.title}>
            <div className="card-label">{item.label}</div>
            <div className="card-value" data-empty={text === null ? "true" : undefined}>
              {text ?? "not recorded"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
