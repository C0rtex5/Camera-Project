# Operator Dashboard & Digital Twin UI

> Sub-project `dashboard-ui` · status **extraction-ready** · cards **SEC-08, SEC-10, SEC-11**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Every safety decision eventually reaches a human. This project owns the browser experience: the
split-view detection hub with the Canvas overlay and the Three.js metric ground twin, the live
camera page, the camera setup page, the restored-asset browser and the operator review controls. It
is pure client code with no server-side import surface, which makes it the easiest project to
extract and test.

## Owned paths

| Path | Contents |
|---|---|
| `src/dashboard/index.html` | Detection hub shell: 2D overlay, 3D twin, metrics, adjudication controls |
| `src/dashboard/live.html` | Minimal live camera page (token in page memory only) |
| `src/dashboard/setup.html` | Camera setup and SEC-01 survey UI |
| `src/dashboard/assets/hub.js` | Frame loop, source switching, staleness expiry, twin rendering |
| `src/dashboard/assets/hub_core.js` | Pure display rules (expiry, validity, risk, PPE) shared with tests |
| `src/dashboard/assets/media.js` | Restored-asset browser panel |
| `src/dashboard/assets/dashboard.css`, `assets/vendor/three.min.js` | Styling and vendored three.js r128 (+ licence) |
| `tests/hub.test.cjs`, `live_dashboard.test.cjs`, `media_panel.test.cjs`, `safety_zone_ui.test.cjs` | Node test suite (66 tests, no browser required) |

## Interfaces it publishes

- **`HubCore` display rules** — pure functions for staleness expiry, packet validity and risk/PPE
  presentation; unit-testable in Node without a DOM. Consumed by `validation-harness`.
- **Telemetry packet consumption** — consumes the `api-gateway` packet contract; a stale packet must
  visibly expire instead of freezing the last frame as if it were live.
- **3D digital twin scene** — metric ground plane, tracks, machinery hulls and hazard geometry; a
  missing WebGL context degrades to a text notice, never a blank page.

## Dependencies

- **Depends on:** nothing at build time (static assets).
- **Runtime consumption only:** the HTTP packet contract of `api-gateway` and the media endpoints of
  `media-pipeline`. This coupling is *not* machine-checked today — a contract test would harden it.
- **Depended on by:** `api-gateway` (serves the assets), `deployment-runtime`, `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py dashboard-ui      # node --test tests/*.test.cjs
```

## Acceptance criteria

1. `node --test tests/*.test.cjs` passes with no network and no browser.
2. A hanging backend expires the displayed frame and metrics instead of showing stale data as live.
3. Hidden pages do not fetch or decode frames.
4. A missing WebGL context still shows the camera image with an explicit notice.

## Risks and open edges

- **Untyped HTTP boundary:** the UI's expectations of the packet are enforced only by Node tests with
  fixture packets; a published JSON Schema from `event-contracts` would remove the guesswork.
- **Vendored `three.js` r128** is old and pinned; an upgrade needs a licence and API-impact review.
- The UI must never imply metric accuracy the calibration does not have — presentation rules are part
  of the safety surface, not decoration.
