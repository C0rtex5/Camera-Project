# Production deployment — one camera

The default image uses CPU inference. The optional NVIDIA image selects CUDA when available and otherwise selects CPU. The CPU image intentionally contains no CUDA libraries and cannot use a GPU. The application keeps one pending frame, defaults to five inference cycles per second, and uses two PyTorch CPU threads. Actual throughput must be measured on the deployment machine. Results older than one second are stale by default; `stale_after_seconds` in the camera profile controls this limit. Status reads remain responsive while the forecasting model runs.

## Required site inputs

- A reachable RTSP stream. ONVIF discovery is not implemented; obtain the RTSP URL from the camera vendor.
- A camera-specific calibration for the exact stream resolution and the same metric datum used by site manifests. `K` is the camera intrinsic matrix, `dist` the distortion coefficients, and `H` maps metric ground coordinates to undistorted pixels. Do not reuse the demo calibration. The example intentionally contains null matrices and will fail validation.
- Local detector weights at `weights/detector.pt`, plus a trained, validated forecasting checkpoint at `weights/forecast.pt`. No forecasting checkpoint is shipped in this repository. The checkpoint must match this GNN implementation; PyTorch Geometric-trained checkpoints require its optional dependency and a matching image build. An untrained checkpoint is not a substitute. COCO detectors do not provide PPE classification; PPE status is unknown for those models.
- Camera username and password, if the camera requires authentication. No application access token is used.

## Start

1. Build and start the demo-first image: `docker compose up --build -d`. The application does not require an environment file, camera, calibration, or model files for the bundled demo.
2. To provide optional deployment variables, create `.env.production` from `.env.production.example`, restrict it with `chmod 600`, and start with `docker compose --env-file .env.production up --build -d`. Camera credentials can remain blank.
3. Open **Camera setup** from the dashboard (or `/setup`).
4. Enter the RTSP address, camera username, and password in separate fields. **Save setup** accepts incomplete drafts, so remaining details can be supplied later. Blank password preserves the saved value; the removal checkbox explicitly clears it.
5. Add measured stream dimensions and calibration when available. Install detector and validated forecasting weights in the mounted `weights/` directory using the explicit `SENTINEL_DETECTOR_WEIGHTS` and `SENTINEL_GNN_CHECKPOINT` paths. The panel lists unmet requirements.
6. Select **Start monitoring** when setup is complete. If already monitoring, save edits and restart the service to apply them. A complete saved setup also starts automatically after restart.
7. `/health` reports process liveness; `/ready` remains 503 until live monitoring is ready. An empty or incomplete setup does not claim monitoring is operational.

The private draft is stored at `data/setup/camera.json` in the persistent data volume with file permissions 0600. Passwords are stored on the server and never returned by the setup API; protect backups of this volume. The setup API opens directly without application login. Saving camera credentials does not contact the camera; monitoring activation does.

Legacy environment/file setup remains supported when no saved panel draft exists. Mount your calibrated JSON at `SENTINEL_CAMERA_CONFIG` and provide its source environment variables to use that workflow. The default Compose file no longer mounts a camera file that may not exist.

For NVIDIA servers with Docker Compose 2.30+ and NVIDIA Container Toolkit installed, use `docker compose -f compose.yaml -f compose.gpu.yaml up --build -d`. This is an x86 server configuration, not a Jetson image. `Dockerfile.edge` is the legacy Jetson artifact and is not the supported production recipe.

Keep a single server worker: multiple workers would open duplicate camera connections. Put an authenticated TLS reverse proxy in front of the localhost-bound service for remote access. Do not place credentials in URLs. Camera credentials are environment variables and are excluded from JSON telemetry.

Keep a single **container** as well. If two containers from this image run on the same host they both publish port 8000 and both carry a restart policy, so they take turns owning the port and the deployment looks like it is flapping. `compose.yaml` pins the project and container name to `sentinelzone` so a second stack fails loudly instead of starting; check the host with `python scripts/docker_instance_audit.py` and see [SINGLE_INSTANCE.md](SINGLE_INSTANCE.md) to diagnose and remove a duplicate.

## SEC-01 camera survey

The setup page includes a **PoC camera survey** workflow. For each candidate, enter the camera ID, physical location, IP/host, RTSP port, stream path, access path, and optional SSH tunnel; the legacy full RTSP address remains supported. The application records TCP reachability, transport type, one-frame resolution, and objective quality measurements (brightness, contrast, sharpness, and screening notes), and logs a structured connectivity blocker when the feed cannot be reached. The survey does not silently make the candidate the active camera.

Use **Select for PoC** only after reviewing a successful frame result. The selected record and redacted Markdown report are available at:

```text
GET  /api/v1/setup/survey
POST /api/v1/setup/survey
POST /api/v1/setup/survey/batch
POST /api/v1/setup/survey/select
GET  /api/v1/setup/survey/report.md
GET  /api/v1/setup/survey/report.json
```

The survey file is stored privately in the persistent data volume as `data/surveys/camera_survey.json` with mode `0600`. Passwords, stream query strings, private keys, and raw video are never written to the survey record. See [CAMERA_SURVEY.md](CAMERA_SURVEY.md) for the site procedure and acceptance evidence.

The application does not scan the plant network or bypass connectivity controls. Camera candidates must be supplied by the site/IT owner. The quality thresholds are PoC screening values, not a substitute for site acceptance criteria or a validated forecasting model.

## Original media and dataset restoration

The original showcase videos and dataset provenance are recorded in:

```text
data/ORIGINAL_ASSETS_MANIFEST.md
data/original_assets_manifest.json
```

The current checkout's Git LFS objects were verified against the upstream OIDs and sizes. The three LinkedIn showcase videos remain in `data/real_videos/`, and the existing `data/test_videos/` proxies and Roboflow splits are intact. The restoration does not change the UI or `src/demo_assets`.

Run the read-only verifier with:

```bash
python scripts/verify_original_assets.py
```

If a clean server has only LFS pointers, use the explicit fetch mode:

```bash
python scripts/verify_original_assets.py --fetch-lfs
```

The construction-safety `data.yaml` path is repository-relative so the restored dataset works on a test server without a machine-specific `D:/...` path.

### Media-complete image

The default image stays slim and ignores `data/`. To bake the verified media and datasets into a separate image tag:

```bash
bash scripts/build_demo_media_image.sh          # builds sentinelzone-ai:demo-media
```

The baked media is read-only. Keep writable state on a separate mount so the assets are never shadowed:

```bash
docker volume create sentinelzone-state
docker run -d --name sentinelzone \
  -p 127.0.0.1:18080:8000 --read-only --tmpfs /tmp:rw,size=256m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e SENTINEL_MODE=production -e SENTINEL_SETUP_FILE=/state/setup/camera.json \
  -e SENTINEL_SURVEY_FILE=/state/surveys/camera_survey.json \
  -e SENTINEL_MEDIA_DIRS=/opt/sentinelzone/data/real_videos,/opt/sentinelzone/data/test_videos \
  -e SENTINEL_DATASET_DIRS=/opt/sentinelzone/data \
  -v sentinelzone-state:/state \
  sentinelzone-ai:demo-media
```

Without a media image, point `SENTINEL_MEDIA_DIRS`/`SENTINEL_DATASET_DIRS` at a restored checkout or a mounted volume.

### Read-only original media endpoints

```text
GET /api/v1/showcase/media                          catalog of restored videos
GET /api/v1/showcase/media/{id}                     one video with probe results
GET /api/v1/showcase/media/{id}/frames/{index}      JPEG frame on demand
GET /api/v1/showcase/media/{id}/file                byte-range MP4 streaming
GET /api/v1/showcase/datasets                       YOLO datasets with split counts
GET /api/v1/showcase/datasets/{id}/manifest         raw data.yaml
GET /api/v1/showcase/media-summary                  counts only
```

`/api/v1/showcase/sources` also reports `original_media` and `datasets` counts. These routes are read-only and stay in the public showcase allow-list; they never scan a network, never fabricate entries, and never write into the media tree. A file below 10 KiB is reported as provenance-only and returns HTTP 409 for frame/stream requests.

### Calibrated detection overlay on restored recordings

Restored recordings are played with real person / heavy-machinery boxes drawn on the decoded frame. `src/perception/frame_calibration.py` guarantees the boxes are expressed in the pixel space of the exact image the browser decodes: inference runs on an aspect-preserving downscale and boxes are mapped back with the same exact factors. Measured against full-resolution inference on the same frame, the mapped boxes agree at **mean IoU 0.9902** with a **0.0** scale error on every 16:9 recording; across the full evaluation run the worst scale error was 4.89e-04 with **0** boxes outside the frame.

```text
SENTINEL_MEDIA_DETECT=0        disable the overlay entirely
SENTINEL_DETECTOR_WEIGHTS      site-trained weights (default: bundled yolov8n.pt)
```

The detector is resolved against the installation root, so it is found whether the service starts from the repository or from a scratch directory. If weights are missing the overlay returns no boxes and an explicit reason rather than pretending to detect.

A restored recording always reports `forecast_status=ILLUSTRATIVE_DEMO`, no tracks, no hazard envelope and no validated forecast. Overlay boxes are a perception overlay on recorded footage, not a validated safety measurement.

### Playback flow of restored recordings

The player was previously limited to roughly 5-10 fps per frame request, which
made 24 fps recordings look choppy. Three changes removed the bottleneck without
touching detection accuracy:

1. **Sequential frame reading** (`media.FrameSource`). A per-request
   `POS_FRAMES` seek into an inter-frame-coded MP4 costs ~79 ms; reading the
   next frame in sequence costs ~2.2 ms. The reader keeps a cursor, walks
   forward, and only seeks on a jump. Measured 12.6 fps -> 876 fps on a 24 fps
   clip.
2. **One video read per request.** The frame is decoded once and shared by the
   JPEG encoder and the detector, instead of being read twice.
3. **Background detection prewarming** (`media_detection._Prewarmer`). Detection
   costs ~60 ms per frame, so a daemon worker decodes the clip sequentially on
   its own handle and fills the same cache ahead of the playhead. Playback then
   only pays the encode cost.

Every displayed frame still receives its own genuine detection: nothing is
interpolated, repeated, dropped or subsampled, and a prewarmed result is
numerically identical to detecting on demand (asserted in
`tests/test_playback_flow.py`).

```text
SENTINEL_MEDIA_PREWARM=0     disable prewarming
SENTINEL_MEDIA_PREWARM=900   frames to warm in the background (default)
```

**Presentation format:** every recording is decoded, scaled to fit and letterboxed onto a canonical **1920x1080** canvas and exposed on a fixed **30 fps** timeline, whatever the source (720p, 1080p, 4K, portrait, 20/24/25/29.97 fps). A 30 fps output index always maps to a real source frame, so no synthetic frame is invented; a 24 fps source is presented by holding frames. Duplicate recordings (identical LFS OIDs) are listed once, preferring the `real_videos` copy, and the alias stays addressable for provenance.

```text
SENTINEL_MEDIA_PREWARM_WORKERS=4   detection workers per warmed clip
SENTINEL_MEDIA_PREWARM_CLIPS=1     clips warmed concurrently (others served inline)
SENTINEL_MEDIA_JPEG_CACHE_MB=512   pre-encoded frame cache budget
SENTINEL_MEDIA_DETECTION_CACHE=1600 detection cache entries
```

Measured on this machine, in process and over HTTP:

| | Before | After |
|---|---|---|
| Serve path, per frame | 202 ms (4.9 fps) | 3.1 ms (321 fps) |
| Over HTTP, first touch | ~9-10 fps | ~9-10 fps (cold) |
| Over HTTP, prewarmed | - | **123-136 fps** |

A 24 fps clip therefore has about 5x headroom at 1x, so the player speed
selector now offers 4x and 8x alongside 0.5x/1x/2x. The control bar shows the
rate actually delivered (`fps served`) so a slow pipeline cannot be mistaken for
smooth playback. Warm-up takes a few seconds per clip: prewarming sustains
~18 detections/s on this CPU, so a 240-frame clip is ready in roughly 13 s and
is instant on replay.

**Measured limitation:** both bundled weights are COCO-80 models, which have no excavator class. Excavator recall on the restored dataset is **0.20**, against 0.93 for `Person` and 0.77 for `wheel loader`. Heavy-machinery coverage is limited by the weights, not by the calibration. See [DETECTION_CALIBRATION.md](DETECTION_CALIBRATION.md) for the full measurement and the training step needed to close the gap.

### Viewing restored assets in the dashboard

The scenario dropdown lists **restored original recordings only**. The curated `src/demo_assets` demonstration bundles are not listed: the catalog is the 17 playable videos restored from the original repository, each marked `(original)` and prefixed , and the frame overlay reads `ORIGINAL RECORDING · NOT LIVE` so a restored clip is never confused with a live feed.

The curated bundles are not deleted. They remain on disk and stay resolvable by direct id on the private `/api/v1/hub/demo/{id}/frames/{index}` route for regression and comparison work, but they are removed from both the private and public scenario listings so they cannot be selected in the UI. The public showcase lists originals only.

The **Restored assets** button in the header opens a detailed panel alongside the dropdown: it lists every restored video with resolution, frame count, duration, size, and LFS provenance, previews frames from the original MP4, plays the file through byte-range streaming, and renders each YOLO dataset with its split counts, class count, license, and raw `data.yaml`.

A restored scenario is a decoded frame only. It carries no detections, tracks, forecast, or benchmark values: its packet reports `forecast_status=ILLUSTRATIVE_DEMO`, an empty `detections` list, `cycle_latency_ms=null`, and a provenance string stating that nothing is attached. Metrics therefore read "Not measured" and the validation matrix stays unvalidated, which is the honest state for unreprocessed footage. Running the original pipeline (YOLO, homography, EKF, GATv2) over a restored clip requires a valid checkpoint and calibration and is a separate, explicit step.

This is additive: the source selector, curated scenario playback, metrics, 3D twin, and review flow keep working, and the panel degrades to an inline error if the catalog is unavailable.


Detection, metric projection, EKF tracking, GNN forecasting, conflict evaluation, incident APIs, permit compilation, agent tools, and the original demo dashboard remain in the source. Legacy demo video APIs are disabled in production. The `/api/v1/hub/demo/` adapter serves explicitly labeled, isolated recorded scenarios without starting inference. Agent calls require an explicitly configured provider and model in production; deterministic example responses are not allowed as a production fallback. Permit compilation additionally requires an actual IFC model and the optional `ifcopenshell` dependency. The existing incident analysis consumes text telemetry; it does not fetch or visually inspect a supplied video URL. Physical relay actuation remains an in-memory flag, not an implemented hardware driver.

The Docker volume retains manifests and incident adjudications. Back up that volume. Container logs rotate. A stopped or stale stream exposes no current safety telemetry; `/health` alone does not indicate operational monitoring. Test disconnect/reconnect, camera resolution changes, inference failure, restart recovery, CPU load, and alert behavior with controlled footage before site operation.

The CPU and GPU images passed isolated RTSP integration tests, including actual RTX 3060 inference and CPU fallback; see [VALIDATION.md](VALIDATION.md). Actual camera connectivity, trained model accuracy, site calibration, and performance on the deployment hardware still require validation. Passing unit tests does not establish those properties.

Backend references: [OpenCV capture timeouts](https://docs.opencv.org/4.10.0/d4/d15/group__videoio__flags__base.html) and [Docker GPU allocation](https://docs.docker.com/reference/compose-file/services/#gpus).

Local CPU smoke measurement (repository sample image, YOLOv8n, ten warmed calls, two PyTorch threads): median 55.7 ms, maximum 59.0 ms, peak process RSS about 416 MiB. This excludes RTSP decoding, GNN execution, and the HTTP service and is not a site performance guarantee.

The checkpoint format and the camera-aligned training workflow are documented in [FORECAST_TRAINING.md](FORECAST_TRAINING.md). Bare legacy checkpoints are not accepted by the production live-camera loader.


## RTSP integration test without a physical camera

After building the CPU image, run `python scripts/docker_rtsp_smoke.py` on a host with the Docker socket available. The test uses an isolated Docker network with no host ports, publishes the repository sample image as H.264 over RTSP/TCP, and runs the actual camera worker, detector, API, and shutdown path. It stops and restarts the publisher and checks readiness and JPEG availability. All test containers, data volumes, and the test network are removed afterward.

The forecasting artifact used by this test is trained for one epoch on explicit synthetic fixtures inside a disposable volume. It is never installed in `weights/forecast.pt` and does not establish site-model accuracy. The MediaMTX test dependency follows its [documented Docker and FFmpeg workflow](https://mediamtx.org/docs/kickoff/install).

The collision engine reports the summed mixture-mode mass of colliding mean trajectories at each forecast instant. This fixes undercounting when several plausible modes predict the same collision. Severity uses probability and time-to-collision from the same instant. This score is not a separately calibrated probability of a real-world incident.

To produce the required camera matrix from measured survey points, follow [CAMERA_CALIBRATION.md](CAMERA_CALIBRATION.md). This creates the camera profile and fingerprint needed by the training workflow without using the demo calibration.


## Image size and verification

The tested CPU image is about 2.66 GB uncompressed; the optional GPU image is about 11.96 GB because it includes CUDA libraries. Use the CPU image for the initial deployment to avoid carrying those libraries. Switching images does not change the calibration/checkpoint contract or the persistent data volume.

To repeat GPU validation on a compatible host, run `python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:production-gpu --gpu`. To verify CPU fallback in that same image, omit `--gpu`. The runner expects CUDA in the first test and CPU in the second.

Both installed Python package inventories were scanned after building, with no known advisories found on the validation date. Recheck advisories when deploying or updating; these scans do not assess the OS, GPU driver, or native CUDA libraries.

## Shared detection hub and offline playback

Both modes serve the same split-view hub at `/`. There is no application-token login or Access window. Recorded scenarios open immediately, and live cameras can be selected directly. Camera setup is optional and opens directly; enter the camera's username and password there when available. Losing live monitoring clears camera overlays, 3D objects, and metrics; it never switches to demo automatically. Playback controls apply only to demo footage. Incomplete camera drafts remain supported.

Three curated scenarios (240 sampled JPEG frames, approximately 20 MB total) ship with the image. All browser scripts, including Three.js and its license, are local. Runtime internet access and production model weights are unnecessary for demo playback. Demo geometry and linear paths are illustrative and explicitly labeled; they are not live GNN predictions or validation results. The comparison table uses “Not measured” for unavailable benchmarks. COCO-only PPE remains unknown.

Live snapshots carry the image, frame identity, dimensions, capture time, histories, model forecast paths, hazards, conflicts, and measured cycle latency together. The browser rejects expired and superseded responses, caps 3D rendering at 30 FPS, and suspends it in hidden tabs. Reviews resolve the displayed frame on the server, expire after five minutes or cache eviction, and retain telemetry snapshots. Demo reviews live in `data/demo_reviews`; live reviews in `data/adjudications`. No live video recording is added.

Optional additional curated bundles can be mounted read-only using `docker compose -f compose.yaml -f compose.demo.yaml up -d`. Place each bundle in `demo-library/<scenario>/` with `manifest.json` and numbered JPEGs (`0000.jpg`, etc.). Use a safe scenario name (letters, digits, underscore, hyphen; maximum 64 characters), at most 1,000 frames and a manifest under 20 MB. Follow the bundled manifest schema in `src/demo_assets/`: title, provenance, playback fps, and per-frame dimensions, timestamp, detections, tracks, illustrative paths and hazards. This is a prepared playback format, not an automatic raw-video importer. `scripts/build_demo_assets.py` documents offline curation from repository clips. The original raw-video APIs remain available only in demo mode.

Application APIs, including camera setup and reviews, no longer require a token. Anyone who can reach the application can use them; the supplied Docker configuration remains bound to localhost. Camera passwords are still stored privately and never returned by the setup API. Demo and live reviews are saved separately. Hub and setup API responses disable caching. Non-root execution, the read-only root filesystem and private persistent data volume are unchanged.

## Optional camera tunnel through an SSH test server

Camera and site names may contain spaces; saved identifiers use hyphens. Invalid fields now report their own validation errors without returning credential values. A failed save does not attempt a network connection.

Use **Check connection** to test the current form without saving it: the application verifies SSH, then receives one RTSP video frame in a bounded subprocess. This check does not require dimensions, calibration, a detector or a forecasting checkpoint, and stores no recording. **Start monitoring** still requires those AI prerequisites. SSH and RTSP authentication are distinct; correct camera credentials cannot replace an SSH private key.

The setup form accepts **SSH private-key file path** and **Verified SSH known-hosts file path** on the application host. Blank paths use `SENTINEL_SSH_KEY_FILE` and `SENTINEL_SSH_KNOWN_HOSTS`. Container paths refer to the mounted files inside the container. Keys remain in files, never in API responses. Host-key verification remains strict. Connection diagnostics distinguish SSH authentication, host verification, timeout/refusal, and camera authentication or stream-path errors without exposing raw process output.

In Camera setup, enter `ssh -p 22022 abhay_m@87.103.13.53` in **SSH tunnel to test server (optional)**. Enter the camera RTSP address as reachable from that server, plus the camera's own username/password. Saving stores a draft only. The existing Start monitoring action opens a loopback-only SSH forward and connects the RTSP/TCP worker through it. Leave the SSH field blank for direct camera access. This is a camera tunnel, not remote deployment or a remote shell.

The current tunnel supports key authentication. On the application host, set `SENTINEL_SSH_KEY_FILE` to a readable private key and `SENTINEL_SSH_KNOWN_HOSTS` to a file containing the independently verified server host key (for the example, host entry `[87.103.13.53]:22022`). Unknown or changed host keys are rejected. Password-only SSH and interactive key passphrases are not supported. No camera password is passed to SSH.

For Docker, use `docker compose -f compose.yaml -f compose.ssh.yaml up --build -d` and provide `ssh/id_ed25519` and `ssh/known_hosts`, readable by container UID 10001. Keep the private key owner-only; do not make it world-readable. This directory is ignored by Git and mounted read-only. Both Dockerfiles install the OpenSSH client. The tunnel uses one process, retries a lost SSH session, and shuts down with the camera service. Camera freshness checks continue to clear stale data during a tunnel outage. RTSP over TCP is supported; RTSPS through this forwarding mode is rejected rather than bypassing TLS hostname checks.

The example host is not contacted when the field is saved. An actual connection requires the SSH key, verified host key, remote camera address, measured calibration and models. Missing tunnel requirements appear in setup status.

### Reproduce the container tunnel checks

Build the disposable SSH fixture with `docker build -f tests/integration/Dockerfile.ssh -t sentinelzone-ai:ssh-test-fixture .`. This fixture is only for isolated tests, never a deployment server. `python scripts/docker_ssh_smoke.py --image sentinelzone-ai:remote-candidate` creates temporary keys, checks strict host verification, exercises real forwarding, restarts the test SSH server, and confirms process cleanup. It exposes no host ports and removes its containers, volumes, network and keys afterward.

`python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:remote-candidate --ssh` additionally runs an actual RTSP stream, detector, synthetic fixture forecaster and API through SSH. It tests publisher interruption and recovery. Use a cached MediaMTX image ID with `--media-image` for offline tests. These checks establish transport and application behavior, not the accuracy of a real site's forecasting model.

The tested container candidates are `sentinelzone-ai:remote-candidate` (CPU) and `sentinelzone-ai:remote-gpu-candidate` (optional NVIDIA). They include the current application with no application-token login. Production image tags are unchanged pending the previously requested deployment approval. A real connection also requires the user's private key, independently verified server host key and camera setup; sample keys from the tests are never installed for that purpose.
