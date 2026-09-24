# SentinelZone AI — standalone CPU image

This bundle packages the current SentinelZone AI application as a CPU-first, single-camera Docker image for Linux amd64.

## Load and run

```sh
sha256sum -c SHA256SUMS
docker load -i sentinelzone-ai-standalone-20260924.tar
docker compose up -d
```

Open <http://127.0.0.1:18080/>. The bundled demo works without a camera, credentials, model weights, or internet access. Camera setup is optional at `/setup`; saved settings remain in the `sentinel-data` volume across restarts.

## Runtime contract

- Image: `sentinelzone-ai:standalone-20260924`
- Image ID: `sha256:304e195640afd04864f1d65ca7a50b0d94c5299495294250eaaa1e7928807d05`
- User: `10001:10001`
- One Uvicorn worker on port `8000`; the Compose host binding is localhost-only.
- Read-only root filesystem, private persistent data volume, tmpfs `/tmp`, dropped capabilities, and no-new-privileges.
- Two CPU inference threads by default. Demo playback is isolated from live inference and uses only the bundled `src/demo_assets` data.
- No application access token is required. Anyone who can reach the bound service can use its setup/live/review APIs, so do not expose the port directly to an untrusted network; put an authenticated TLS reverse proxy in front of it for remote access.

## Enable live monitoring

The image intentionally does not contain model weights, camera credentials, or private setup data. Put a camera-compatible detector at `models/detector.pt` and a validated, camera-fingerprint-matched forecast checkpoint at `models/forecast.pt`, then start with the optional live overlay:

```sh
docker compose -f compose.yaml -f compose.live.yaml up -d
```

The setup panel must also contain the measured stream dimensions, calibration, RTSP URL, and camera username/password. The connection check can test a stream before models and calibration are available; **Start monitoring** requires the complete contract. No production forecast checkpoint is fabricated or bundled.

## Optional SSH camera tunnel

For a camera reached through a test server, mount a private key and independently verified `known_hosts` file under `ssh/` and add the optional overlay:

```sh
docker compose -f compose.yaml -f compose.live.yaml -f compose.ssh.yaml up -d
```

Keep key files readable by UID `10001`; never put them in the image or commit them. The SSH command and camera credentials are separate.

## Optional GPU image

The optional CUDA image uses the same application and falls back to CPU when no NVIDIA device is exposed:

```sh
docker build -f Dockerfile.gpu -t sentinelzone-ai:standalone-20260924-gpu .
```

The validated candidate GPU image was `sha256:8f09425401dd1210dc24e3161374dd315209c16dc218690920f39ad4b3e9593f` (approximately 12 GB uncompressed). It is not included in this CPU archive; the CPU image is the recommended default.

## Stop and remove

Keep saved settings when stopping:

```sh
docker compose down
```

Remove the persistent settings volume only when you intentionally want to erase local setup/review data:

```sh
docker compose down -v
```

The image has passed controlled H.264/TCP RTSP decode, disconnect/reconnect, graceful-shutdown, offline setup/demo, CPU, CUDA, and CPU-fallback checks. Those checks do not establish physical-camera connectivity, site calibration, or forecasting accuracy; those require the real camera, measured survey points, reviewed recordings, and a trained checkpoint.
