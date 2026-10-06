# Validation Harness & Evidence

> Sub-project `validation-harness` · status **extraction-ready** · cards **SEC-09, SEC-12**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

A safety product is only as credible as its evidence. This project owns the policy engine that
validates the twelve SEC cards, the isolated test runner, the original-asset verifier, the
comparative evaluation harness and every stored evidence artefact. Its central rule: an item that
requires physical-site evidence can never be closed with demonstration evidence.

## Owned paths

| Path | Contents |
|---|---|
| `scripts/sprint_validation.py` | Sprint validation policy engine: verdicts, evidence classes, physical-site gate |
| `scripts/run_tests.py` | unittest runner against an isolated temporary data root |
| `scripts/attribute_slice_failures.py` | Attributes every failing test in a slice log to a documented exclusion; non-zero exit if anything is unclassified |
| `scripts/verify_original_assets.py` | Git LFS pointer detection and restored-media checksum verification |
| `scripts/build_demo_assets.py`, `scripts/build_demo_media_image.sh` | Demo asset/media image builders |
| `deploy/VALIDATION.md`, `deploy/VALIDATION_FRAMEWORK.md` | Verified results, remaining work, framework rules |
| `deploy/validation/**` | Stored evidence: SEC-01..12 records, image audits, detector evaluations, agent audits |
| `data/ORIGINAL_ASSETS_MANIFEST.md`, `data/original_assets_manifest.json` | Original asset inventory and hashes |
| `tests/__init__.py`, `tests/evaluate_comparative.py`, `tests/test_original_assets.py` | Shared test package marker, comparative evaluator, asset tests |

## Interfaces it publishes

- **Sprint validation policy engine** (`sprint_validation.py`) — exactly one verdict per numbered
  item; PASS needs evidence, BLOCKED needs a named prerequisite, physical-site items cannot be closed
  with demo evidence. Consumed by `deployment-runtime`, `agentic-supervisor`.
- **Isolated test runner** (`run_tests.py`) — runs the suite against a temporary data root so
  repository incidents and manifests are never overwritten. Consumed by `deployment-runtime`.
- **Original asset verifier** (`verify_original_assets.py`) — reports missing assets instead of
  assuming them. Consumed by `media-pipeline`.
- **Stored evidence set** (`deploy/validation/**`) — every claim points at a reproducible command, an
  exit code and a stored artefact.

## Dependencies

- **Depends on:** all twelve other sub-projects (it validates the whole platform).
- **Depended on by:** none — it is the portfolio's top-level gate.

## How to run it

```bash
python scripts/run_tests.py
python scripts/sprint_validation.py --help
python scripts/verify_original_assets.py
```

## Acceptance criteria

1. `python scripts/run_tests.py` executes the suite in an isolated temporary data root.
2. The policy engine rejects a PASS without an evidence reference and rejects physical-site items
   closed with demonstration evidence.
3. `verify_original_assets.py` distinguishes LFS pointers from real media.
4. `deploy/VALIDATION.md` is regenerated from evidence, never hand-edited into a claim.

## Risks and open edges

- **Needs the pinned runtime stack:** the harness could not be executed in the analysis sandbox (no
  torch/fastapi/opencv installed), so its command is verified by inspection; the container is the
  only place it is authoritative.
- **Honesty is the control:** the physical-site evidence gate is what keeps the product from
  overclaiming; weakening it would silently convert demos into safety claims.
- Node-side UI tests live in `dashboard-ui`; the two runners must be invoked together for a full gate.
