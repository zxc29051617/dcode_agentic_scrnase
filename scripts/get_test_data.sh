#!/usr/bin/env bash
# Fetch the public datasets the test suite and the demos use.
#
#   bash scripts/get_test_data.sh            # list what is needed and what is present
#   bash scripts/get_test_data.sh matrices   # 54 MB  — enough for most tests
#   bash scripts/get_test_data.sh fastq      # 18 GB  — the FASTQ route
#   bash scripts/get_test_data.sh all
#
# Nothing here is in the repository: 27 GB does not belong in git. Every test
# that needs a dataset skips when it is absent, so a fresh clone runs green
# without any of this — it just tests less.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA="${DATA_DIR:-$PROJECT_DIR/data}"
CF=https://cf.10xgenomics.com/samples/cell-exp

log(){ echo "[$(date +%T)] $*"; }

# name|subdir|url|what it is for
MATRICES="
pbmc_1k_v2|10x_public|$CF/3.0.0/pbmc_1k_v2/pbmc_1k_v2_filtered_feature_bc_matrix.h5|3' v2 chemistry
pbmc_10k_v3|10x_public|$CF/3.0.0/pbmc_10k_v3/pbmc_10k_v3_filtered_feature_bc_matrix.h5|ten times the cells
PBMC_LT_ChromiumX|10x_public|$CF/6.1.0/500_PBMC_3p_LT_Chromium_X/500_PBMC_3p_LT_Chromium_X_filtered_feature_bc_matrix.h5|LT kit on Chromium X
pbmc_1k_protein_v3|10x_public|$CF/3.0.0/pbmc_1k_protein_v3/pbmc_1k_protein_v3_filtered_feature_bc_matrix.h5|CITE-seq, 17 antibody features
neuron_1k_v3|10x_public|$CF/3.0.0/neuron_1k_v3/neuron_1k_v3_filtered_feature_bc_matrix.h5|MOUSE, cross-species check
"

# name|url|what it is for
FASTQS="
pbmc_1k_v3|$CF/3.0.0/pbmc_1k_v3/pbmc_1k_v3_fastqs.tar|human 3' v3, the reference end-to-end run
pbmc_1k_v2|$CF/3.0.0/pbmc_1k_v2/pbmc_1k_v2_fastqs.tar|human 3' v2 — the chemistry check that R1 length gets wrong
neuron_1k_v3|$CF/3.0.0/neuron_1k_v3/neuron_1k_v3_fastqs.tar|mouse, the non-human end-to-end run
"

status() {
  echo "matrices  -> $DATA/10x_public   (54 MB)"
  while IFS='|' read -r name sub url note; do
    [ -z "$name" ] && continue
    [ -f "$DATA/$sub/$name.h5" ] && mark="ok  " || mark="  --"
    printf "  %s %-20s %s\n" "$mark" "$name" "$note"
  done <<< "$MATRICES"
  echo
  echo "fastq     -> $DATA/<name>/       (18 GB total)"
  while IFS='|' read -r name url note; do
    [ -z "$name" ] && continue
    [ -d "$DATA/$name/${name}_fastqs" ] && mark="ok  " || mark="  --"
    printf "  %s %-20s %s\n" "$mark" "$name" "$note"
  done <<< "$FASTQS"
  echo
  echo "references are separate: see reference/README.md"
}

get_matrices() {
  mkdir -p "$DATA/10x_public"
  while IFS='|' read -r name sub url note; do
    [ -z "$name" ] && continue
    target="$DATA/$sub/$name.h5"
    if [ -f "$target" ]; then log "have $name"; continue; fi
    log "downloading $name"
    curl -sSf --retry 3 -o "$target.part" "$url" && mv "$target.part" "$target"
  done <<< "$MATRICES"
}

get_fastqs() {
  while IFS='|' read -r name url note; do
    [ -z "$name" ] && continue
    if [ -d "$DATA/$name/${name}_fastqs" ]; then log "have $name fastqs"; continue; fi
    mkdir -p "$DATA/$name"
    log "downloading $name fastqs (GB-scale, this takes a while)"
    curl -sSf --retry 3 -o "$DATA/$name/$name.tar" "$url"
    tar xf "$DATA/$name/$name.tar" -C "$DATA/$name"
    rm -f "$DATA/$name/$name.tar"          # the extracted copy is the one we keep
    printf 'dataset=%s\nsource_url=%s\nnote=%s\n' "$name" "$url" "$note" \
      > "$DATA/$name/SOURCE.txt"
  done <<< "$FASTQS"
}

case "${1:-status}" in
  status)   status ;;
  matrices) get_matrices; status ;;
  fastq)    get_fastqs; status ;;
  all)      get_matrices; get_fastqs; status ;;
  *) echo "usage: bash scripts/get_test_data.sh [status|matrices|fastq|all]"; exit 2 ;;
esac
