#!/usr/bin/env bash
# Put a Cell Ranger reference under this project's reference/ directory.
#
#   bash scripts/link_reference.sh human /path/to/T2T_CHM13v2_RefSeqLiftoff_v5_3
#   bash scripts/link_reference.sh human --copy /path/to/ref   # copy instead of link
#   bash scripts/link_reference.sh                             # list what is registered
#
# Why this script exists: every path inside this project must be project-local.
# Code and config only ever say `reference/<dirname>`; this symlink is the one
# place that knows where the bytes actually live, so moving them (another disk,
# another machine, a real copy) never touches Python.
#
# The registry lives in src/species.py and this script asks it — the directory
# name is NOT repeated here. Two tables that have to agree is the drift that
# leaves you with a reference the pipeline cannot find.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REF_ROOT="${REF_ROOT:-$PROJECT_DIR/reference}"

log(){ echo "[$(date +%T)] $*"; }
die(){ log "FATAL: $*"; exit 1; }

SPECIES_ARG="${1:-}"
if [ -z "$SPECIES_ARG" ]; then
  echo "usage: bash scripts/link_reference.sh <species> [--copy] <path-to-reference>"
  echo
  echo "registered species:"
  ( cd "$PROJECT_DIR" && python3 -c "
from src import species
for name in species.known():
    p = species.SPECIES_PROFILES[name]
    r = p.reference
    print(f'  {name:<8} {r.dirname}')
    print(f'           {r.note}')
" )
  echo
  echo "Anything else: build a reference from FASTA + GTF, then pass its path here"
  echo "and set reference.transcriptome explicitly (see reference/README.md)."
  exit 0
fi

MODE="link"
if [ "${2:-}" = "--copy" ]; then MODE="copy"; shift; fi
SOURCE="${2:-}"
[ -n "$SOURCE" ] || die "no source path given. usage: bash scripts/link_reference.sh <species> [--copy] <path>"

# ---- 1. ask the registry for the directory name ---------------------------
DIRNAME="$(cd "$PROJECT_DIR" && python3 -c "
import sys
from src import species
p = species.profile('$SPECIES_ARG')
if p is None:
    sys.stderr.write(
        f\"no reference registered for species='$SPECIES_ARG'. \"
        f\"Registered: {', '.join(species.known())}.\n\"
        'Pass a path anyway and set reference.transcriptome explicitly.\n')
    raise SystemExit(2)
print(p.reference.dirname)
")" || exit 2

TARGET="$REF_ROOT/$DIRNAME"

# ---- 2. validate the source is a real Cell Ranger reference ---------------
SOURCE="$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")"
[ -d "$SOURCE" ] || die "source is not a directory: $SOURCE"
[ -f "$SOURCE/reference.json" ] || die \
  "source has no reference.json, so it is not a Cell Ranger reference: $SOURCE"

# ---- 3. already there? ----------------------------------------------------
# Guard on reference.json rather than the directory: a broken symlink or a
# half-finished copy is not a reference and must not be treated as one.
if [ -f "$TARGET/reference.json" ]; then
  if [ -L "$TARGET" ]; then
    log "already linked: $TARGET -> $(readlink "$TARGET")"
  else
    log "already present: $TARGET"
  fi
  exit 0
fi
[ -e "$TARGET" ] && { log "removing broken/incomplete $TARGET"; rm -rf "$TARGET"; }

# ---- 4. link or copy ------------------------------------------------------
mkdir -p "$REF_ROOT"
if [ "$MODE" = "copy" ]; then
  NEED_GB="$(du -sBG "$SOURCE" | cut -f1 | tr -d 'G')"
  FREE_GB="$(df -BG --output=avail "$REF_ROOT" | tail -1 | tr -d ' G')"
  [ "$FREE_GB" -gt "$NEED_GB" ] || die "need ${NEED_GB}G, only ${FREE_GB}G free at $REF_ROOT"
  log "copying ${NEED_GB}G $SOURCE -> $TARGET (this takes a while)"
  cp -a "$SOURCE" "$TARGET"
else
  log "linking $TARGET -> $SOURCE"
  ln -s "$SOURCE" "$TARGET"
fi

# ---- 5. verify it resolves and report which genome it holds ---------------
[ -f "$TARGET/reference.json" ] || die "after $MODE, $TARGET/reference.json is not readable"
( cd "$PROJECT_DIR" && python3 -c "
import json
from pathlib import Path
from src import species
meta = json.loads(Path('$TARGET/reference.json').read_text())
genomes = meta.get('genomes') or []
seen = species.identify_reference(meta)
print(f\"  genomes: {', '.join(genomes)}\")
print(f\"  version: {meta.get('version', '?')}\")
if len(seen) == 1:
    print(f'  species: {seen.pop()}')
elif len(seen) > 1:
    print(f\"  species: multiple ({', '.join(sorted(seen))}) - barnyard/PDX reference\")
else:
    print('  species: not recognised from the reference itself (fine for a custom build)')
" )
log "DONE. Use it as: reference/$DIRNAME"
