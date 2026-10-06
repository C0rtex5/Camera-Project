# Camera Onboarding, Survey & Tunnelling

> Sub-project `camera-onboarding` · status **extraction-ready** · cards **SEC-01, SEC-02**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

Inference cannot start without a camera. This project covers the whole pre-inference journey:
private setup drafts that persist but never start inference half-configured, the SEC-01 survey record
with objective frame-quality evidence, bounded non-recording connection checks, and the loopback-only
SSH tunnel used to reach a camera endpoint from a test server.

## Owned paths

| Path | Contents |
|---|---|
| `src/api/camera_setup.py` | `SetupDraft`, `SetupStore`: private, atomic camera setup persistence |
| `src/api/camera_survey.py` | `SurveyStore`: SEC-01 survey records, quality evidence, redacted report |
| `src/api/connection_check.py` | `check_connection`: bounded, non-recording probe with classified failures |
| `src/api/ssh_tunnel.py` | `CameraTunnel`: one loopback-only RTSP/TCP forward over SSH |
| `deploy/CAMERA_SURVEY.md` | SEC-01 survey procedure and evidence rules |
| `tests/test_camera_setup.py`, `test_camera_survey.py`, `test_connection_check.py`, `test_ssh_tunnel.py` | Onboarding tests |

## Interfaces it publishes

- **`SetupStore`** — atomic camera drafts; an incomplete draft never starts inference and secrets are
  never echoed back. Consumed by `api-gateway`, `deployment-runtime`.
- **`SurveyStore` + redacted report** — candidate resolution, frame-quality evidence, connectivity
  blockers, selected camera. Consumed by `api-gateway`, `validation-harness`.
- **`check_connection`** — objective quality evidence (resolution, contrast, sharpness) or a
  classified failure; never records media. Consumed by `api-gateway`, `dashboard-ui`.
- **`CameraTunnel`** — refuses non-loopback binds, verifies the SSH host key. Consumed by
  `api-gateway`, `deployment-runtime`.

## Dependencies

- **Depends on:** `spatial-calibration` (calibration profile shape for onboarding).
- **Depended on by:** `api-gateway`, `deployment-runtime`, `validation-harness`.
- **Documented coupling debt (2 edges):** `camera_setup.py → src/api/live_service.py` and
  `ssh_tunnel.py → src/api/live_service.py`. Resolution: accept a worker-control protocol from the
  gateway, or move the camera configuration model into `event-contracts`.

## How to run it

```bash
python scripts/run_project_tests.py camera-onboarding
```

## Acceptance criteria

1. Saving an incomplete camera draft never enables inference.
2. A survey run produces a redacted report with resolution and quality evidence.
3. A failing endpoint returns a classified error, not an unhandled exception.
4. The tunnel binds loopback only and verifies the SSH host key.

## Risks and open edges

- **Highest confidentiality risk in the product:** this project handles camera credentials and SSH
  keys. Secret handling needs its own review checklist before extraction.
- **Mutual dependency on `api-gateway`** (declared debt) means the two cannot yet be extracted into
  separate repositories independently.
- The survey flow is documented as requiring **physical-site evidence** for several items; the
  harness in `validation-harness` is what prevents demo evidence from closing them.
