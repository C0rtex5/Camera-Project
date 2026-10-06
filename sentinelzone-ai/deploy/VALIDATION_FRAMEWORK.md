# Sprint validation framework

This is the standing procedure for validating the SentinelZone AI cards against
the running application and the repository. It is the reusable harness behind
`deploy/validation/DEEPSEEK_HARNESS_SEC01_12_RESULTS.md`.

The framework is **evidence-gathering only**. A validation run never changes
application code, never rewrites a test to make it pass, and never edits the
source card checkboxes.

## Why it exists

Most failure modes in a PoC are not crashes, they are claims. A demo bundle
looks like a live camera, a disposable RTSP fixture looks like a site stream,
and a restored recording looks like plant evidence. This framework makes that
distinction mechanical rather than a matter of memory.

## The five rules

1. **One verdict per numbered item.** `PASS`, `FAIL`, `BLOCKED`, `NOT TESTED`.
   `PASS` requires an observed result. `FAIL` means an executable check produced
   a contrary result or the required behaviour is absent. `BLOCKED` means a named
   external prerequisite prevents the check. `NOT TESTED` means the check was
   available and was not run; the reason is recorded.
2. **Evidence class on every observation.** `SOURCE/UNIT`, `DEMO/RECORDING`,
   `SYNTHETIC RTSP`, `PHYSICAL SITE CAMERA`. These are not interchangeable.
3. **The site gate.** An item flagged as requiring physical-site evidence cannot
   be closed with a recording, a fixture or a public dataset. This is the rule
   that stops demonstration evidence from being reported as site validation, and
   the harness rejects such a record as a policy violation.
4. **A card passes only when every item passes.** A partial card is reported with
   its item-level results, never as complete.
5. **Record failures as evidence.** The gap list is an output, not an omission.
   The source checkboxes are never edited to match a verdict.

## Running a validation

```bash
# 1. baseline: identity, environment, live state
#    record git commit AND file hashes, because the worktree is uncommitted
# 2. discover, do not assume: app URL, /health, /ready, live camera count,
#    image id and build date for every image you intend to use as evidence
# 3. run the tests and capture exact commands, exit codes and counts
# 4. exercise the application; record what could not be exercised and why
# 5. build one record per numbered item
# 6. generate the report (the harness validates policy, then renders)
.venv-prod/bin/python scripts/sprint_validation.py \
    --evidence deploy/validation/<evidence>.json \
    --out deploy/validation/<REPORT>.md --strict
```

`--strict` makes the run fail on any policy violation, so an inadmissible
report cannot be produced by accident.

## Files

| Path | Purpose |
|---|---|
| `scripts/sprint_validation.py` | The harness: verdict vocabulary, evidence classes, the site gate, the 100-item registry, tally and report rendering |
| `deploy/validation/deepseek-harness-2026-09-29/` | Evidence for the current run: baseline logs, detector measurement, per-item evidence JSON, and the generator that produced it |
| `deploy/validation/DEEPSEEK_HARNESS_SEC01_12_RESULTS.md` | The rendered report |

## Adapting it to the next sprint

* Add the card items to `CARDS` in `scripts/sprint_validation.py`. The registry
  is the single source of truth for what must be adjudicated; the harness
  refuses to render a report with a missing or unknown item.
* Mark site-dependent items in `REQUIRES_PHYSICAL`. Keep this list conservative
  and justify any item removed from it in a comment, so a later reader can audit
  the judgement rather than inherit it silently.
* Keep the evidence JSON beside the logs it cites so any row can be re-checked.
