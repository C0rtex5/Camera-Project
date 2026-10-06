# Safety Event & Data Contracts

> Sub-project `event-contracts` · status **extraction-ready** · cards **SEC-06, SEC-07**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Five projects exchange objects: the edge emits events, the API broadcasts packets, the agent
compiles manifests, the UI renders risk. This sub-project is the single definition of those
objects — risk levels, business states, event types, the structured safety event, shift manifests,
dynamic envelope configuration and triage verdicts — so no layer can invent its own vocabulary.

## Owned paths

| Path | Contents |
|---|---|
| `src/schemas/safety_event.py` | `RiskLevel`, `BusinessState`, `EventType`, `SafetyEvent`, `build_event`, `to_broadcast_payload` |
| `src/schemas/contracts.py` | `ShiftSafetyManifest`, `DynamicEnvelopeConfig`, `IncidentTriageVerdict`, entity/severity enums |
| `config/default_config.json` | Original default settings, thresholds and envelope specification |
| `deploy/SAFETY_EVENT_CONTRACT.md` | Field-by-field event contract, state conditions and honest-evidence rules |
| `deploy/RISK_LEVELS.md`, `deploy/SETTINGS_ALIGNMENT.md` | Risk-level definitions and settings-alignment record |
| `tests/test_contracts.py`, `tests/test_settings_alignment.py` | Contract and settings-preservation tests |

## Interfaces it publishes

- **`SafetyEvent` (Pydantic v2)** — camera, zone/machine, timestamp, risk level and event type are
  mandatory; serialization is stable for WebSocket broadcast. Consumed by `edge-runtime`,
  `api-gateway`, `agentic-supervisor`, `dashboard-ui`.
- **`RiskLevel` / `BusinessState`** — SAFE / ATTENTION / RISK map onto documented business states;
  no project may add a fourth level locally. Consumed by `hazard-tracking`, `edge-runtime`,
  `dashboard-ui`.
- **`ShiftSafetyManifest` / `DynamicEnvelopeConfig`** — validated shift manifests with exclusion
  envelopes, persisted and dispatched without reinterpretation. Consumed by `agentic-supervisor`,
  `edge-runtime`.

## Dependencies

- **Depends on:** nothing (leaf project — deliberately, so it can be depended on by everyone).
- **Depended on by:** `hazard-tracking`, `edge-runtime`, `api-gateway`, `agentic-supervisor`,
  `validation-harness`.

## How to run it

```bash
python scripts/run_project_tests.py event-contracts
```

## Acceptance criteria

1. Every emitted event validates against the contract; a missing required field fails closed.
2. `deploy/SAFETY_EVENT_CONTRACT.md` matches the code field-for-field.
3. `config/default_config.json` remains the single source of original settings, and tests prove it is
   not silently rewritten.
4. Adding a required field requires an explicit contract version bump.

## Risks and open edges

- **Most-depended-on node in the portfolio.** An unversioned field change breaks edge, API and UI at
  once — this project needs the strictest review gate and a change log.
- **Operational drift:** the defaults file is edited both by developers and by operators; the
  settings-alignment test is the current guard.
- Kept dependency-free on purpose: any import added here would create a cycle through every consumer.
