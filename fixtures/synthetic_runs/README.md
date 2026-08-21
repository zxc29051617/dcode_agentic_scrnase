# Synthetic run fixture

Three fabricated run directories the gateway is developed and tested against.
Neither came from `python -m src.run`. Nothing here is real: no FASTQ, no
`.h5ad`, no real donor/sample identity, no real hostname, no real git commit,
no real API key or endpoint.

| run | state | has report |
|---|---|---|
| `demo-2026-0001` | completed | yes |
| `demo-2026-0002` | halted at `apply_cell_qc_filter`'s human gate | no |
| `demo-2026-0003` | completed | yes |

Regenerate with:

```bash
python fixtures/synthetic_runs/generate_fixture.py
```

Deterministic and idempotent — re-running overwrites the same bytes and
rewrites `MANIFEST.sha256`. Verify the fixture has not drifted:

```bash
cd fixtures/synthetic_runs && sha256sum -c MANIFEST.sha256
```

This is a development fixture for `services/gateway`, not the golden-run
evidence fixture described in `docs/deep_agents_architecture.md` §10 — that one
is derived from a real public FASTQ run and does not exist yet.
