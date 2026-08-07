#!/usr/bin/env bash
# What the runs/ directory is costing, and which runs are safe to remove.
#
# Nothing is deleted. Every step writes its own AnnData so a run can be resumed
# from disk instead of from a checkpoint (see src/persistence.py), and that is
# also why a run costs a few hundred MB. Which of those are still worth keeping
# is a judgement about the work, not about the bytes, so it is left to a person.
#
#   bash scripts/run_disk_usage.sh            # summarise runs/
#   bash scripts/run_disk_usage.sh <dir>      # summarise somewhere else
set -euo pipefail

RUNS="${1:-runs}"
if [ ! -d "$RUNS" ]; then
    echo "no such directory: $RUNS"
    exit 0
fi

total=$(du -sh "$RUNS" 2>/dev/null | cut -f1)
count=$(find "$RUNS" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
echo "$RUNS: $total across $count run(s)"
echo

printf '%-34s %8s  %s\n' "run" "size" "state"
find "$RUNS" -mindepth 1 -maxdepth 1 -type d | sort | while read -r dir; do
    size=$(du -sh "$dir" 2>/dev/null | cut -f1)
    # A run with a report finished; one without stopped somewhere.
    if [ -f "$dir/build_report/report.html" ]; then
        state="reported"
    elif [ -f "$dir/audit.jsonl" ]; then
        state="incomplete"
    else
        state="empty"
    fi
    printf '%-34s %8s  %s\n' "$(basename "$dir")" "$size" "$state"
done

echo
echo "The reports and the audit log are small; the .h5ad per step is not:"
find "$RUNS" -name '*.h5ad' -printf '%s\n' 2>/dev/null \
    | awk '{s+=$1} END {if (NR) printf "  %d .h5ad files, %.1f GB\n", NR, s/1073741824}'
echo
echo "To keep a run's conclusions but drop its intermediates:"
echo "  find $RUNS/<run_id> -name adata.h5ad -delete"
echo "That leaves the report, the figures, markers.csv, run_metadata.json and"
echo "audit.jsonl — everything the report was built from — but the run can no"
echo "longer be resumed, and steps that were skippable will re-run."
