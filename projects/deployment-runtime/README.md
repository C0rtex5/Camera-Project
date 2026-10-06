# Deployment, Images & Operations

> Sub-project `deployment-runtime` · status **extraction-ready** · cards **SEC-02, SEC-12**
> Machine-readable manifest: [project.json](project.json) · portfolio index: [../INDEX.md](../INDEX.md)

## Why this sub-project exists

The product ships as a container on an edge host. This project owns the image definitions, the
hardened Compose recipes, the systemd unit, the production runbook and the disposable-container smoke
tests that gate an image before anyone calls it deployable.

## Owned paths

| Path | Contents |
|---|---|
| `Dockerfile` | CPU-first runtime image (app + assets + offline demo bundles) |
| `Dockerfile.gpu`, `Dockerfile.edge`, `Dockerfile.demo-media` | CUDA, edge and demo-media variants |
| `compose.yaml`, `compose.gpu.yaml`, `compose.ssh.yaml`, `compose.demo.yaml` | Hardened deployment recipes and overlays |
| `.dockerignore`, `.env.production.example` | Build context exclusions and the production environment contract |
| `deploy/PRODUCTION.md`, `deploy/sentinelzone-edge.service` | Runbook and the systemd edge unit |
| `deploy/SINGLE_INSTANCE.md` | Diagnosis and fix for two containers taking turns owning port 8000, plus the one-time name/volume migration |
| `scripts/docker_instance_audit.py` | Read-only audit: who supervises each container, which host port is contested, exit 1 on duplicates |
| `tests/test_docker_instance_audit.py` | Nine tests against a stubbed `docker` CLI (no daemon needed), including the reported flapping fixture |
| `scripts/docker_setup_smoke.py`, `docker_rtsp_smoke.py`, `docker_ssh_smoke.py` | Image-level gates using disposable containers/keys |
| `tests/integration/Dockerfile.ssh`, `tests/integration/rtsp_probe.py` | Integration fixtures for the tunnel/RTSP smoke paths |

## Interfaces it publishes

- **CPU runtime image** (`sentinelzone-ai:standalone`) — serves the app on `:8000` with no camera
  configured; credentials, keys and weights are not baked in. Consumed by `validation-harness`.
- **Hardened Compose recipe** — read-only root filesystem, dropped capabilities, `no-new-privileges`,
  named data volume, pinned environment contract. Consumed by `validation-harness`.
- **SSH tunnel overlay** — adds the camera tunnel path for RTSP smoke tests without exposing the
  tunnel beyond loopback. Consumed by `validation-harness`.
- **systemd edge unit** — restart-on-failure with an explicit environment file.

## Dependencies

- **Depends on:** every code sub-project (it packages them all) plus the shared pinned dependency
  scope (`requirements-runtime.txt` / `.lock`).
- **Depended on by:** `validation-harness`.

## How to run it

```bash
docker build -f Dockerfile -t sentinelzone-ai:standalone .
docker compose -f compose.yaml up -d
python scripts/docker_setup_smoke.py        # also: docker_rtsp_smoke.py, docker_ssh_smoke.py
```

## Acceptance criteria

1. `docker build -f Dockerfile` succeeds and the image answers `/health`.
2. The Compose recipe starts with a read-only root and a writable data volume.
3. Each smoke script bootstraps and tears down its own disposable resources.
4. No image bakes in camera credentials, SSH keys or private setup data.

## Risks and open edges

- **Not executable in the analysis sandbox:** image builds need network access for pinned wheels, so
  the commands above are verified by inspection only. Treat the first CI run as the real gate.
- **Four Dockerfiles × four Compose files drift easily:** the smoke scripts are the only automated
  coupling; a matrix job should build and smoke every variant.
- **Pinned dependencies live in the shared platform scope**, so a dependency bump is a cross-project
  change requiring review from this project and every consumer.
