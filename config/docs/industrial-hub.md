# Industrial camera hub

The supported entrypoint is `src.api.app:app`. Run exactly one application worker: it owns the camera capture and inference threads. The pilot supports four fixed local RTSP cameras. Viewing survives inference failure. No equipment or relay actuation is exposed.

## Start on a Linux PC/server

Install Docker Engine and Compose. For NVIDIA acceleration, also install a compatible driver and NVIDIA Container Toolkit and configure Docker GPU access. Build-time package downloads require internet; runtime does not. Only the launcher performs GPU availability checks; a container with required GPU reservations can fail before application CPU fallback has a chance to run.

```bash
bash scripts/start-hub.sh auto
# Force a CPU deployment:
bash scripts/start-hub.sh cpu
```

Open http://localhost:8000 and sign in. Add local operator/viewer accounts with the same command and `--role operator` or `--role viewer`. Passwords must contain at least 12 characters. Replacing an account invalidates its existing sessions. Login sessions expire after eight hours. No cloud AI keys are necessary.

The bundled `yolov8n.pt` is a general COCO detector: it detects people and common vehicles, but **does not provide specialized machinery or PPE detection**. PPE is reported as unknown unless the selected model explicitly identifies positive/negative PPE classes. Use a site-validated construction model to enable those capabilities. Mount replacement weights read-only and set `SENTINEL_WEIGHTS` to their container path. Models are mapped using their class names. Missing/invalid weights disable inference; there are no automatic downloads or synthetic detections.

The GPU image uses CUDA 12.4 PyTorch wheels; the CPU image uses CPU wheels. The default `auto` mode probes inference, falls back to CPU on initialization/inference failure, clears affected tracking state, and resumes with fresh frames. CPU mode may deliver a lower AI rate. `cuda` mode requires GPU inference and does not silently fall back. An administrator can retry GPU from the dashboard. Inference is serialized across cameras to bound accelerator memory while capture remains independent.

## Camera credentials and network

Camera configuration accepts credential-free `rtsp://` or `rtsps://` URLs. Enter the camera username and password in the configuration dialog. They are stored separately from public configuration in the local SQLite database (owner-only file permissions) and survive restarts or container replacement. Leave both fields blank when editing to preserve existing credentials, or select removal to clear saved authentication. Replacement requires both fields. Camera responses return only a credentials-configured flag; credentials are excluded from configuration, audit records, validation errors, and logs. Database backups now include camera credentials and use owner-only permissions.

For externally provisioned credentials, you can still use a JSON secret file in `deploy/secrets/`, for example a file named `camera_1` with `username` and `password` fields. Create it using a text editor, restrict access to the deployment owner/container user, and select its filename in camera configuration. The directory is mounted read-only at `/run/secrets`; secrets are excluded from Git and the build context. The API never returns the stored username or password. OpenCV/FFmpeg logs are suppressed to prevent backend URL disclosure.

Ensure the host can reach the camera subnet. TCP transport is the default. Open/read timeouts are three seconds, reconnect delay backs off to 30 seconds, and frames older than two seconds are unavailable. RTSP decode timestamps are local receipt times; they are not proof of sensor-to-display latency. Use camera low-latency/substreams and measure source-side buffering separately.

By default the hub binds to localhost. For other workstations, use a TLS reverse proxy on the industrial management network, set `SENTINEL_ORIGIN` to its exact HTTPS origin and `SENTINEL_COOKIE_SECURE=true`. Route HTTP and WebSocket traffic, disable proxy buffering on streams, and keep the underlying Docker port private. Do not configure the reverse proxy to trust arbitrary forwarded hosts. The default local panel opens without a username or password and grants local operator access to configuration, reviews, and zone approval. Mutations still require same-origin requests and `X-Sentinel-Request: hub`. Optional API session authentication is available with `SENTINEL_AUTH_REQUIRED=true`; provision accounts using `docker compose run --rm hub python -m scripts.hub_admin user admin`. In that explicit mode, monitoring routes and streams require a session. The normal panel is intended for direct local access.

## Calibration, zones, and incidents

Each camera has an optional calibration JSON with `width`, `height`, 3x3 `K`, distortion coefficients `dist`, 3x3 ground-to-image `H`, matching `pixel_references` and `ground_references` (at least four surveyed non-collinear points), and a named `coordinate_frame`. RMSE must be at most 0.15 m. Resized image anchors are restored to calibration resolution; aspect-ratio changes disable metric inference. Calibration is checked against supplied survey references at configuration time. Re-survey after camera movement, vibration-induced shifts, lens changes, or lighting/visibility changes; the pilot does not automatically track surveyed markers or certify live calibration drift.

Example shape (replace all values with a real survey):

```json
{"width":1920,"height":1080,"K":[[1000,0,960],[0,1000,540],[0,0,1]],"dist":[0,0,0,0,0],"H":[[100,0,0],[0,100,0],[0,0,1]],"pixel_references":[[100,100],[1000,100],[1000,800],[100,800]],"ground_references":[[1,1],[10,1],[10,8],[1,8]],"coordinate_frame":"surveyed-floor"}
```

Without valid calibration, viewing and pixel detection remain available, but metric TTC/forecasts are unavailable. The production pilot uses constant-velocity forecasts. To use a learned checkpoint in the legacy runtime, supply its adjacent `.pt.json` metadata with a `validation_id` and matching `sha256`; missing or invalid checkpoints fail explicitly. Track matching is one-to-one and uses actual elapsed frame time. Exclusion zone polygons must use the camera's coordinate frame. Operators stage drafts; administrators review and approve them. Approval saves the effective camera configuration and restarts that camera's analytics. Direct administrator camera edits are also audited. No fabricated BIM geometry is substituted.

Warning/critical proximity and explicit noncompliant PPE generate incidents with a 30-second per-camera cooldown. Evidence includes a snapshot of the analyzed frame and a bounded MJPEG AVI clip sampled at 1 fps, with up to ten seconds before and five seconds after the event. Download clips for playback; browser AVI support varies. Evidence encoding has a bounded queue; missing or failed evidence is reported explicitly. Human verdicts persist and false-positive/uncertain cases can be exported as JSONL for active learning. Rules triage does not claim to inspect video or verify spotters.

## Persistence and maintenance

The `hub-data` named volume contains SQLite state and evidence. Container replacement preserves cameras, users, sessions, reviews, manifests, and audit history. Default evidence retention is seven days with a 2 GiB cap; the database and audit history are retained separately. Configure `SENTINEL_RETENTION_DAYS` and `SENTINEL_EVIDENCE_MAX_BYTES` for site policy. Leave space for the database and current evidence write; the cap is enforced after writing and during periodic cleanup. Disk failures surface as degraded readiness/storage errors. Liveness controls Docker health; individual camera failures do not trigger restart loops. Docker's restart policy restarts exited processes, not merely unhealthy containers.

Back up with the hub stopped to keep database references and evidence consistent. A backup must be outside the active data directory. Use a maintenance bind mount accessible by UID 10001:

```bash
docker compose stop hub
docker compose run --rm -v "$PWD/backups:/backups" hub python -m scripts.hub_admin backup /backups/site-backup
# Restore only into an empty replacement data volume; hub remains stopped:
docker compose run --rm -v "$PWD/backups:/backups:ro" hub python -m scripts.hub_admin restore /backups/site-backup
docker compose up -d
```

Back up camera secret files separately with restricted access. Test restoration into a fresh volume before relying on backups. Logs are rotated (three 10 MiB files). Graceful shutdown stops capture, inference, and evidence workers. Do not run multiple replicas against one camera registry. Retention applies to evidence; periodically assess SQLite/audit growth and archive according to site policy.

The recorded-video demo is disabled by default. Set `SENTINEL_ENABLE_DEMO=true` and mount recorded videos read-only under `/opt/sentinel/data/test_videos` and/or `/opt/sentinel/data/real_videos`. Open `/demo/`. Recorded scenario REST routes retain their original paths and follow the selected access mode. Demo calibration is illustrative and must never be treated as a commissioned camera calibration. Three.js is bundled locally; there is no CDN dependency. Legacy graph functions stage drafts and require real BIM geometry; production workflows use the hub's database and human approval.

## Commissioning and 72-hour pilot

Run tests with Python 3.11 or 3.12, CPU PyTorch, `requirements-hub.txt`, and pytest. Test a disconnected camera, a stale stream, loss of internet, model errors, invalid calibration, and storage exhaustion before site use. Run both CPU and GPU deployments on target hardware and record per-camera delivered AI fps and processing/end-to-end latency. Do not assume a 120 ms budget or CPU/GPU performance equivalence.

```bash
python -m scripts.pilot_monitor --url http://localhost:8000 --hours 72 --output pilot-results.jsonl
```

The monitor samples readiness every 30 seconds and writes a local JSONL report. When optional API authentication is enabled, supply `--username admin`; only then is a password requested and expired sessions renewed. Include all four cameras, actual resolutions/codecs, and representative worker/equipment/PPE scenes. Verify camera reconnects, power/container restarts, backup restoration, operator workflows, and evidence retention. Evaluate missed detections and false alarms using independently labeled site footage. Validate day/night lighting, dust, occlusion, vibration, camera movement, and calibration references. Hardware benchmarking and the 72-hour pilot must be performed on the deployment machine; repository tests cannot establish site accuracy.

## Reproducible offline container check

The smoke check exercises the real bundled detector and local APIs with networking disabled and a read-only image; the test state is temporary.

```bash
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,size=256m --tmpfs /var/lib/sentinel:uid=10001,gid=10001 -e OMP_NUM_THREADS=2 -i sentinelzone-hub:cpu python - < tests/docker_smoke.py
```
