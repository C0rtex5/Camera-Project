# Agentic Safety Supervisor

> Sub-project `agentic-supervisor` · status **extraction-ready** · cards **SEC-09, SEC-10, SEC-12**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

The cognitive layer: a LangGraph ReAct supervisor with six real domain tools, a multi-provider LLM
factory with a deterministic offline reasoner, Pipeline 1 (permits-to-work → BIM exclusion
envelopes) and Pipeline 2 (multimodal incident adjudication plus active-learning curation). It must
work air-gapped and it must never invent a safety fact.

## Owned paths

| Path | Contents |
|---|---|
| `src/graph/supervisor_agent.py` | `SupervisorAgentState` + 6 tools + ReAct loop (`build_supervisor_agent`) |
| `src/graph/llm_provider.py` | `get_agent_llm`, `DeterministicAgentLLM` (offline, deterministic fallback) |
| `src/graph/context_pipeline.py` | `build_context_pipeline`: PTW text → validated manifest → edge injection |
| `src/graph/adjudication_pipeline.py` | `build_adjudication_pipeline`: VLM triage → incident record + active-learning queue |
| `data/incidents/**`, `data/adjudications/**`, `data/active_learning/**` | Forensic records, human adjudications, curated retraining queue |
| `tests/test_supervisor_agent.py`, `tests/test_pipelines.py` | Agent tool and pipeline tests |

## Interfaces it publishes

- **`build_supervisor_agent`** — multi-turn ReAct loop returning an answer plus the executed tool
  trace; every tool returns real repository state, never a placeholder. Consumed by `api-gateway`,
  `dashboard-ui`.
- **`get_agent_llm`** — OpenAI / Anthropic when explicitly configured, `DeterministicAgentLLM`
  otherwise; air-gapped operation must not require network access. Consumed by `api-gateway`,
  `deployment-runtime`.
- **Pipeline 1 / Pipeline 2** — Pipeline 1 persists a validated `ShiftSafetyManifest` and pushes
  envelopes to the edge runtime; Pipeline 2 persists a forensic incident record and curates edge
  cases exactly once per event. Consumed by `api-gateway`, `validation-harness`.

## Dependencies

- **Depends on:** `event-contracts`, `edge-runtime`, `spatial-calibration`.
- **Depended on by:** `api-gateway`, `validation-harness`.
- **Documented coupling debt (3 edges):** `context_pipeline.py → src/api/app.py`,
  `supervisor_agent.py → src/api/app.py`, `supervisor_agent.py → src/api/demo_service.py`. Each has
  a named resolution in [project.json](project.json); until they are paid, this project and
  `api-gateway` are mutually dependent.

## How to run it

```bash
python scripts/run_project_tests.py agentic-supervisor
```

## Acceptance criteria

1. The supervisor answers a telemetry question offline with `DeterministicAgentLLM` and a visible
   tool trace.
2. Pipeline 1 rejects invalid permit text instead of writing a manifest.
3. Pipeline 2 writes one incident record and one queue entry per event.
4. After the coupling debt is paid, this project imports no `src.api` module (the verifier proves it).

## Risks and open edges

- **Circular coupling with the gateway** is the single largest structural defect found during the
  analysis; it is declared, machine-checked and scheduled in
  [../MIGRATION_PLAN.md](../MIGRATION_PLAN.md) rather than hidden.
- **LLM cost and availability** are production concerns; only the deterministic path can be verified
  offline, so every agent claim must state which provider produced it.
- Active-learning and incident files are **production data** living in the repository tree; extraction
  should separate code from runtime data directories.
