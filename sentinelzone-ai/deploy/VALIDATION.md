# Validation record — 2026-09-23

- CPU image built on the available Docker Engine: `sentinelzone-ai:production`.
- Image ID: `sha256:a617cbfff0e9f806fa876e426769d9baf3081eef921de96fd28d93b5f5c256a8`.
- Image smoke test exited 0 as UID 10001 with a read-only root filesystem, dropped capabilities, temporary storage, and an isolated data volume. Verified application imports, public login page, liveness, authentication rejection, unavailable-camera readiness (503), disabled demo routes, and lifecycle cleanup. Camera service was mocked for this test; no real camera or model was substituted silently.
- Full isolated regression suite: **82 tests passed**, 57.701 seconds, Python 3.11.16 and the pinned CPU dependencies. Includes original APIs, demo playback, core tracking/conflict code, production authentication and lifecycle, empty-scene detection, camera disconnect and calibration mismatch, invalid model outputs, checkpoint metadata validation, synthetic training artifact generation, mixture-mode collision mass, time-aligned risk thresholds, and status responsiveness during slow inference.
- Dependency consistency check passed. Compose YAML parsed. Git whitespace checks passed.
- CPU detector-only smoke measurement: 55.7 ms median / 59.0 ms maximum for ten warmed calls on `dam_sample_0.jpg`, two PyTorch threads, approximately 416 MiB peak process RSS. These are not full camera-pipeline performance figures.
- The CPU image is approximately 2.70 GB uncompressed; it retains PyTorch and the original agent dependencies. No claim of a tiny image is made. The application defaults to one camera, two CPU threads, bounded latest-frame storage, and a five-inference-per-second cap.

Still pending: real camera RTSP connectivity, measured calibration, representative reviewed training/validation recordings, camera-aligned forecast training, end-to-end alert and latency testing, and actual relay integration if required. The repository's relay flag is not a physical actuator.

The original local video file was already represented as modified by Git LFS before work began and was left untouched. Test-generated repository incident/manifest changes were restored; subsequent runs use isolated temporary output directories.

## Additional production corrections

- Collision risk now sums the probability mass of colliding forecast modes at each time step. It no longer misses a collision merely because probability is split across several similar forecast paths, nor combines an early low-probability event with an unrelated later peak.
- Camera status and capture no longer wait on the forecasting lock. The default freshness limit is one second and can be set per camera using `stale_after_seconds`.
- The RTSP integration runner creates only isolated test resources, uses no host port bindings, and deletes its containers, volumes, and network afterward. The test stream is a local repository image encoded as H.264; it is not a physical security camera.
- The final image also passed an unmocked RTSP/HTTP integration test through Uvicorn: real H.264/TCP decoding, YOLO inference, authenticated readiness and JPEG responses, publisher interruption, stale-telemetry removal, publisher restart, recovered monitoring, and logged graceful shutdown. The checkpoint was trained and evaluated on explicit synthetic fixtures solely for the test and remained inside the disposable volume. MediaMTX dependency image: `sha256:00ef3d1a243f3769ca36769b47df241a0c269c83a7735005b817505c49adb651`.

## Dependency refresh

The runtime was upgraded to PyTorch 2.14.0 / Torchvision 0.29.0 and patched API and agent libraries after advisory scanning identified vulnerabilities in the previous pins. All 82 regression tests pass with the upgraded dependencies and camera setup. The installed CPU image inventory (86 packages) reports no known Python-package advisories on 2026-09-23. This is not an OS, driver, CUDA-native-library, or comprehensive security assessment.

Evidence: [CPU package inventory](validation/cpu-image-inventory.json), [Python advisory scan](validation/cpu-python-audit.json), and [RTSP integration result](validation/cpu-rtsp.log).

Runtime lock SHA-256: `e1640d93cad9b9eed30754138be495e8eaa3cc4463aa7a6243842d11e471c2ea`.

## GPU and fallback validation

- Optional image built and tagged locally as `sentinelzone-ai:production-gpu`, ID `sha256:8b7efb57e2155810cfc812bcaef6f83eb18c0dd0e4b411d1ba5c97592c5a7476`.
- CUDA 12.6 / PyTorch 2.14.0 image passed the same real RTSP/Uvicorn integration test on the available NVIDIA GeForce RTX 3060. The detector and health endpoint reported `cuda:0`.
- The same image, with no GPU device exposed, passed the full test and reported `cpu`. This verifies automatic selection at startup; it does not promise recovery from a GPU failing during inference.
- Both checks cover authenticated readiness/JPEG, publisher loss, stale telemetry removal, reconnect, and graceful shutdown. Fixtures and training data were synthetic; no physical camera was connected.
- The GPU image is approximately **12.00 GB uncompressed**, versus **2.70 GB** for CPU. CUDA dependencies account for the larger optional image. CPU remains the default for the initial one-camera deployment.
- Installed GPU image inventory: 105 Python packages, no known Python-package advisories found on 2026-09-23. Native CUDA/driver/OS vulnerabilities were not assessed by this scan.

Evidence: [GPU package inventory](validation/gpu-image-inventory.json), [Python advisory scan](validation/gpu-python-audit.json), [CUDA integration](validation/gpu-rtsp.log), and [CPU fallback](validation/gpu-cpu-fallback.log).

## Browser freshness regression

Four JavaScript failure-scenario tests pass: a hanging next request clears previously displayed video/telemetry at expiry, a delayed image is discarded, reconnecting rejects an older in-flight response, and request timeout clears current content. The browser uses the camera's configured freshness threshold and conservatively includes request elapsed time. A watchdog runs independently of request completion. Run with `node tests/live_dashboard.test.cjs`; these tests exercise the page script with a simulated browser clock and network, not visual browser rendering.

Evidence: [dashboard regression output](validation/live-dashboard-tests.log).

The images were rebuilt after the freshness correction. CPU, CUDA, and GPU-image CPU fallback integration tests all passed again; installed package inventories are unchanged from the advisory-scanned versions.

## Separate camera setup panel

The production interface opens with no camera profile, source URL, detector, or forecasting checkpoint. `/setup` is a public login shell; its setup APIs require the application token in both production and demo modes. Drafts can omit camera data and remain editable. Passwords are persisted in a mode-0600 file in the data volume, are never included in API responses, and can be explicitly cleared. Setup responses disable caching. Saving does not interrupt active monitoring; starting is a separate action.

The full 82-test Python suite passed; final lifecycle/authentication checks (4 tests) and all 4 browser-script freshness tests passed. A real browser verified unlocking, saving an empty draft, and reopening the panel after server restart. Both final Docker images passed setup-only tests with a read-only root and no network; see [CPU setup](validation/cpu-setup.log) and [GPU setup](validation/gpu-setup.log).

The CPU image passed the existing legacy RTSP configuration test. The GPU image passed RTSP tests via saved setup and the start endpoint with both CUDA and CPU fallback (`scripts/docker_rtsp_smoke.py --setup`, plus `--gpu` for CUDA). These trained explicit synthetic fixture checkpoints, saved the matching calibration via the setup API, activated monitoring, verified forecast availability and JPEG output, interrupted the publisher, and recovered. They do not depend on real camera credentials.

Repeat setup-only image verification with `python scripts/docker_setup_smoke.py --image sentinelzone-ai:production` (or `production-gpu`). Both final image package inventories match the advisory-audited versions.

## Restored showcase hub

The final CPU and GPU builds above include the shared showcase-style hub, setup styling, local browser dependencies, and three isolated demo bundles. The full Python suite passed 82 tests. Seven additional hub JavaScript tests passed: stalled-response expiry, delayed image rejection, source-generation rejection, truthful defaults/PPE, unavailable WebGL, expired authentication, and hidden-tab fetch suspension. The four older dashboard tests describe the retained legacy page, not coverage of the new hub.

Browser checks at desktop size verified the split camera/ground-plane layout, original dark palette and panel arrangement, blue/amber detections, demo play/pause, stepping, scrubber, speed, orbit/zoom, a frame-specific review, source switching, missing-camera clearing, unavailable supervisor, setup navigation and incomplete saving. Dynamic measurements and explicit demo labels intentionally differ from the showcase. Automated setup tests additionally verified private credentials and persisted reload across application lifecycles.

Both images passed setup and demo tests with networking disabled and read-only root filesystems. Demo frames were authenticated, browser dependencies were local, and the legacy inference service remained uninitialized. Ten in-process demo API requests measured median 4.04 ms / peak process RSS 334.1 MiB on CPU and 4.26 ms / 594.6 MiB with the GPU image (without GPU inference). These are playback API measurements, not browser FPS, camera latency, or inference benchmarks.

Final RTSP tests passed on CPU, RTX 3060 CUDA, and GPU-image CPU fallback. The probe also asserted atomic JPEG/frame dimensions/timestamp, exported model-path consistency, a live review bound to the captured frame, and snapshot unavailability after disconnect. A deterministic model-output unit test verifies selected-mode path export. The fixture checkpoints are synthetic and establish pipeline behavior only. Installed package inventories are unchanged from the advisory-scanned versions.

Evidence: [Python results](validation/python-tests.log), [hub script tests](validation/hub-tests.log), and the setup/RTSP logs linked above. Real camera calibration, forecasting accuracy and deployment-machine end-to-end performance remain unvalidated until the actual camera and representative reviewed recordings are available.

## Optional camera setup follow-up

Added a direct “Explore demo — no camera needed” entry that selects demo even when a live camera exists, optional setup labels, and a setup skip link. Application-token authentication remains required. The browser verified demo entry and the skip link; all seven hub regressions passed again. No inference, credential, or camera behavior changed.

Refreshed images: CPU `sha256:37888d94cca11a141bdce9dda231ad87a3d19ba285b6ccf4fdc0cf5909a6f181`; GPU `sha256:e3bf5ffbbf394d3368ebfde108c24c9768dd68e6c850e9763757adc62321e3ac`. The full RTSP and Python results above apply to the preceding build; this follow-up repeats offline setup/playback smoke checks for the UI-only change.

## Credential-free showcase correction

The previous optional-setup follow-up still required an application token for demo access. This is corrected: a fresh visit now opens the bundled demo without any credentials. The live access window is dismissible. Public GET-only showcase routes expose only bundled assets and no camera configuration or mounted recordings. Live/setup/private hub APIs and stored reviews remain authenticated. Anonymous review buttons report a local preview, explicitly not saved.

Verified a fresh browser reload without entering a token, playback, preview adjudication, and closing the Access window. Seven hub JavaScript regressions and three backend hub tests passed (5.937 seconds for the latter), including public demo access and rejection of unauthenticated live/setup/review requests. The preview server was restarted and both open hub tabs refreshed.

Current production image: `sha256:dbfe1db59bfb75f8f6f33c5969b2079a8b7fbf503598a13f85daec73a549059d`. Offline anonymous-demo and protected-setup checks passed; see `validation/public-demo-cpu.log`.

Current production-gpu image: `sha256:36f3cc1bf9ea9bb17988e449200516546cdbd6fd1a5eb38b48e67f22f7f31633`. Offline anonymous-demo and protected-setup checks passed; see `validation/public-demo-gpu.log`.

## Application login removed at user request

This supersedes the authentication descriptions above: application-token enforcement and its UI have been removed for now. Camera setup loads immediately, and the camera's own username/password remain the credentials used for its RTSP connection. Live and demo review APIs operate without an application login. Anyone who can reach the app can use its APIs; Docker remains bound to localhost. Password storage remains mode 0600 and passwords are never returned by the setup API.

Full Python regression suite: 82 passed in 56.990 seconds. Six applicable hub JavaScript regressions passed; the obsolete token-expiry UI test was removed. Browser checks verified the absence of Access/login controls, directly editable setup, and incomplete draft saving without credentials. The preview server runs without an application token.

Both candidate images passed offline playback and setup persistence without SENTINEL_API_TOKEN or Authorization headers. CPU candidate: `sha256:1777491c50e1a161c2444927a9149a032ba6f8873e28c15b76edac9695237241`; GPU candidate: `sha256:926d8775b9344bcc5fc666ce2dffea0a4fc9a6a5d7fb2d6aa756d6f0b2cf56c3`. Evidence: `validation/no-token-cpu.log` and `validation/no-token-gpu.log`. Automatic approval review blocked promotion to production tags pending explicit approval of token removal across setup, live, hub and review APIs. Production tags remain on the previous authenticated build; the local preview uses the updated source.

## SSH camera tunnel field

Camera setup now persists an optional `ssh -p PORT username@server` field. It forwards RTSP/TCP through a loopback-only OpenSSH connection, using a host-provided private key and strictly verified known-hosts file. Saving does not contact the remote server. The existing monitoring activation opens the tunnel; it retries a lost SSH process and closes on service shutdown. Shell commands and extra SSH options are rejected. Camera passwords are excluded from SSH command arguments. Dockerfiles include openssh-client; `compose.ssh.yaml` mounts private SSH files read-only.

87 Python tests passed in 53.226 seconds, including five new tunnel tests for parsing, credential separation, draft persistence, failure cleanup and shutdown. Browser verification saved the supplied SSH example and confirmed it survived reload, with missing key/host prerequisites displayed. No actual connection to the example server was attempted: its private key, verified host key and remote camera details are not configured. Docker recipes changed; images were not rebuilt or promoted for this follow-up. Earlier production promotion remains pending the separately requested authentication approval.

## Remote tunnel container verification

Built CPU `sentinelzone-ai:remote-candidate` (`sha256:9e7a8093485843b2303eba24f122711038bbb3461fe53c64e3eaeab05d3e7397`) and GPU `sentinelzone-ai:remote-gpu-candidate` (`sha256:d075365f4bf0a27134b5925d62657a9562d5382978d598f831f92ec9d0baa5a5`). Both include OpenSSH and passed offline setup/playback plus real private-key SSH forwarding, wrong-host-key rejection, server restart recovery and graceful tunnel shutdown. The probes ran as the image's non-root user with read-only root filesystems, isolated data volumes and no published ports.

RTSP decoding, inference, synchronized snapshots, frame-bound reviews, publisher loss/recovery and shutdown passed on CPU, RTX 3060 CUDA, and GPU-image CPU fallback. A further combined CPU test routed the actual RTSP stream through the SSH tunnel using the saved camera setup field and passed the same pipeline checks. The test SSH keys and servers were disposable. All test resources were removed by the runners. Evidence: `validation/remote-ssh-{cpu,gpu}.log`, `remote-setup-{cpu,gpu}.log`, `remote-rtsp-{cpu,gpu,fallback}.log`, and `remote-rtsp-through-ssh.log`.

The actual test server has not been contacted: its key path, verified known-hosts file and camera RTSP details are still required. These tests use a synthetic forecasting checkpoint, not a site-validated model. Production tags remain unchanged; the remote candidates are ready for a later approved deployment.

## Current standalone candidate validation — 2026-09-24

The refreshed CPU candidate was built from the current worktree with `Dockerfile` and tagged `sentinelzone-ai:standalone-20260924-candidate`:

- Image ID: `sha256:304e195640afd04864f1d65ca7a50b0d94c5299495294250eaaa1e7928807d05`
- Uncompressed size: approximately 2.71 GB
- Runtime user: `10001:10001`; liveness health check: `/health`; one Uvicorn worker
- The image contains the local dashboard assets and three bundled demo scenarios, but no repository data, `.env`, camera configuration, SSH keys, detector weights, or forecast checkpoint.

The pinned-image regression gate passed **91 Python tests** in an isolated read-only, network-disabled container. Both browser suites passed: 6 hub tests and 4 legacy live-dashboard tests. The setup smoke test passed with no camera, no network, a fresh data volume, incomplete setup persistence, private mode-0600 password storage, and no demo inference worker.

The refreshed optional GPU candidate is `sentinelzone-ai:standalone-20260924-gpu-candidate`, image ID `sha256:8f09425401dd1210dc24e3161374dd315209c16dc218690920f39ad4b3e9593f` (approximately 12.0 GB). Its setup smoke test passed. Controlled H.264/TCP RTSP integration passed for:

- the CPU image on CPU;
- the GPU image with an NVIDIA device exposed (`cuda:0`); and
- the GPU image without a device exposed (CPU fallback).

All three paths verified real stream decode, detector/API output, disconnect clearing, reconnect recovery, and graceful shutdown. The RTSP fixture and synthetic checkpoint are test-only; they do not validate the physical camera, site calibration, or forecasting accuracy.

## SEC-01 camera survey workflow — 2026-09-27

The application now implements the SEC-01 evidence workflow in `src/api/camera_survey.py`, `src/api/connection_check.py`, and the setup page:

- Each candidate attempt records a private, credential-free survey record.
- A successful connection records frame width/height plus brightness, contrast, Laplacian-sharpness, and screening-status measurements.
- Failed attempts record structured connectivity blocker codes such as `camera_authentication`, `stream_not_found`, `connection_refused`, `connection_timeout`, and `ssh_host_key`.
- A camera can be selected only after a frame was received; the selected record and redacted Markdown report are exposed at `/api/v1/setup/survey` and `/api/v1/setup/survey/report.md`.
- Survey data is stored under the private runtime volume at `data/surveys/camera_survey.json` with mode `0600`; passwords, stream query strings, private keys, and video are excluded.

Verification completed:

- 109 Python tests passed in the pinned source environment, including twelve SEC-01 tests and six original-asset restoration tests covering structured inventory fields, readiness status, selection requirements, dispositions, LFS media, dataset parity, and portable dataset configuration.
- 6 hub browser tests and 4 live-dashboard tests passed.
- The rebuilt CPU image passed an in-container API smoke test for survey creation, credential redaction, selection, report generation, and setup-page exposure.
- Both the source application at `http://127.0.0.1:18083/` and the rebuilt local container at `http://127.0.0.1:18080/` expose the survey API and setup section.
- A real refused local RTSP endpoint was exercised through the survey API: it logged `camera_open_failed`, generated the report, and correctly refused selection with HTTP 409.
- A controlled H.264/TCP RTSP stream was surveyed end to end: the app received a 640×480 frame, recorded quality status `acceptable` and `meets_poc_minimum`, selected the record, and generated the redacted report.
- The bounded batch API (`POST /api/v1/setup/survey/batch`) was tested with multiple valid candidates and an incomplete candidate; valid records were retained and the invalid candidate was returned in a redacted `errors` list.
- The setup page exposes the same batch workflow as **Validate an IT-provided candidate list (JSON)**; both running instances served the updated control.
- A controlled structured-inventory RTSP survey recorded location, host, port, normalized stream path, direct transport, TCP reachability, 1280×720 frame, and acceptable quality; the redacted report contained the inventory fields.
- The machine-readable report endpoint `/api/v1/setup/survey/report.json` returned `card_id=SEC-01` on both running instances without credentials.
- Blocked survey attempts now support bounded dispositions (`open`, `reviewed`, `approved`, `rejected`, `deferred`); the setup page can mark a block reviewed and the report retains the disposition note.
- Selection now requires a returned frame plus documented physical location, host/IP, RTSP port, and stream path; a frame-only record cannot be selected.
- The survey response now includes an explicit `sec01_status` readiness object listing missing requirements; it becomes complete only after inventory, stream evidence, block review, and selection.

## Original asset restoration verification — 2026-09-27

- `python scripts/verify_original_assets.py` passed with 20/20 LFS objects matching upstream OIDs and sizes.
- The three LinkedIn showcase videos decoded successfully with positive frame counts.
- Construction-safety splits passed 8/2/2 image-label parity.
- Roboflow splits passed 307/57/34 image-label parity.
- The construction-safety `data.yaml` machine-specific `D:/...` path was replaced with a repository-relative path.
- No UI files were changed by the restoration step; the current `src/demo_assets` and dashboard remain untouched.

## Original media in the running application — 2026-09-28

- The restored assets were not reachable in the earlier running instances: both had an empty `data/` tree, so only the curated `src/demo_assets` showcase was exposed.
- `src/api/media.py` now indexes the restored videos and datasets, resolving `SENTINEL_MEDIA_DIRS`, then the repository checkout, then the working directory.
- Docker: `Dockerfile.demo-media` + `scripts/build_demo_media_image.sh` build `sentinelzone-ai:demo-media` with the verified assets baked in; state is mounted separately at `/state` so the read-only media is never shadowed.
- Source: the app resolves the repository `data/` tree even when launched from a separate working directory.
- Both running instances report 18 original videos (17 playable), 2 datasets, and the same showcase scenario mapping.
- `GET /api/v1/showcase/media`, `/media/{id}`, `/media/{id}/frames/{index}`, `/media/{id}/file`, `/datasets`, `/datasets/{id}/manifest`, and `/media-summary` return 200/206 on both instances.
- Byte-range MP4 streaming returned HTTP 206 with the requested length; frame extraction returned `image/jpeg`.
- The three showcase videos are tagged with their existing scenario IDs, and the 258-byte upstream sample is reported as provenance-only (HTTP 409 for frame/stream).
- New showcase routes were added to the read-only public allow-list; no write, upload, or network-scan path was added.
- 121 Python tests passed, including 12 media-catalog tests; 6 hub and 4 live-dashboard browser tests passed; the media image passed the in-container setup smoke test.
- No dashboard HTML, CSS, or JavaScript file was modified by this work.

## Restored assets in the app UI — 2026-09-28

- A **Restored assets** button in the dashboard header opens a panel that renders the restored videos and datasets from the read-only showcase catalog.
- The panel lists all 18 original videos with group, resolution, frame count, duration, size, and LFS OID, and previews frames from the original MP4 rather than a re-encode.
- The three showcase videos are badged `SHOWCASE`; the 258-byte upstream sample is badged `NOT PLAYABLE` and disables preview/playback.
- Each dataset renders with split image/label counts, class count, license, and a `data.yaml` manifest button.
- The panel is additive: the hub source selector, scenario playback, metrics, 3D twin, and review flow were not modified.
- The panel degrades to an inline error when the catalog is unavailable rather than breaking the hub.
- 7 new browser tests (`tests/media_panel.test.cjs`) cover rendering, auto-selection, provenance-only handling, playback, manifest loading, and catalog failure.
- Verified live on both instances: panel markup present, `media.js` served, 18 videos (17 playable), 2 datasets, showcase frame fetch returned HTTP 200 `image/jpeg` (148010 bytes, valid JPEG).
- Regression after the change: 121 Python tests passed; 17 browser tests passed (6 hub, 4 live dashboard, 7 media panel); container smoke passed; baked media confirmed at 504M with 9 primary videos.

## Restored videos in the hub scenario dropdown — 2026-09-28

- The scenario dropdown was populated only from the 3 curated `src/demo_assets` bundles, so 17 restored recordings were reachable solely through the separate assets panel.
- `src/api/hub.py` now contributes every playable restored video to the hub catalog with an `original:` id prefix, exposing the real `frame_count` and `fps` from the source file.
- `/api/v1/hub/sources` returns 20 scenarios: 3 curated plus 17 original, each tagged with `kind` (`curated`/`original`) and `group` (`real_videos`/`test_videos`).
- `/api/v1/hub/demo/{id}/frames/{index}` decodes frames on demand from the original MP4 for original scenarios; a frame request beyond the real frame count returns HTTP 404.
- Restored packets are explicitly telemetry-free: empty `detections`, `cycle_latency_ms=null`, `forecast_status=ILLUSTRATIVE_DEMO`, and a provenance string stating that no detections, tracks, forecast or benchmark values are attached. Metrics read "Not measured" rather than showing invented values.
- The dropdown marks originals with an `(original)` suffix, and the frame overlay shows `ORIGINAL RECORDING · NOT LIVE` while one is selected.
- The public showcase path rule was widened to accept the `:` separator in scenario ids.
- `tests/test_hub.py` was updated to assert the curated set stays at 3 while originals are present, and that an original frame returns a valid, telemetry-free packet.
- Verified live on both instances: 20 scenarios (3 curated, 17 original); frame 40 of `Worker_in_excavator_blind_spot` returned HTTP 200, 172807 bytes, valid JPEG, 1280x720, 0 detections.
- Regression: 121 Python tests passed; 17 browser tests passed.

## Original-media-only scenario catalog — 2026-09-28

- The hub scenario dropdown previously mixed 3 curated `src/demo_assets` bundles with 17 restored originals; the curated entries are now removed from the listing.
- `src/api/hub.py` splits the catalog: `original_demos()` is the listed scenario set, `curated_demos()` keeps the bundles resolvable by direct id.
- Both `/api/v1/hub/sources` and `/api/v1/showcase/sources` return 17 entries, all with `kind=original`; no curated id or title appears in either listing.
- The curated bundles are not deleted from disk and remain addressable on the private frame route for regression comparison; the public showcase frame route serves originals only.
- The curated sort-priority map was removed, so the dropdown is ordered by id.
- `tests/test_hub.py` now asserts the curated list is empty rather than exactly 3; `scripts/docker_setup_smoke.py` asserts every listed scenario is original.
- Verified live on both instances: 17 scenarios, all original, none of the 3 curated titles listed; frame 10 returned a valid 232426-byte JPEG with 0 detections; public showcase lists 17 originals and its frame endpoint returns a JPEG.
- Regression: 121 Python tests passed; 17 browser tests passed; container smoke passed (`OFFLINE_HUB_OK: restored original recordings`).

No plant camera has been supplied to this environment. The application and asset restoration are verified, but no physical camera has been selected or site-validated yet. The remaining SEC-01 evidence requires the site/IT camera list, reachable RTSP details, credentials entered through the private setup form, and network-owner disposition of any logged blocks.


## Detection frame calibration and model precision — 2026-09-29

- Frames are calibrated through `src/perception/frame_calibration.py`: inference runs on an aspect-preserving downscale and boxes are mapped back with exact factors, because the dashboard scales boxes by `canvas / frame_width`.
- Aspect ratio is preserved exactly: scale error 0.0 at 1280x720, 1920x1080, 3840x2160 and 2160x3840; 1.2e-05 at 607x1080.
- Validated against full-resolution inference on the same frame across 19 sampled frames of 4 recordings: **mean IoU 0.9902**, 100% class agreement on matched boxes.
- Full evaluation run: maximum calibration scale error 4.89e-04 and **0 boxes outside the frame** on every image and video.
- `scripts/evaluate_detector.py` measures precision on `data/roboflow_downloaded` (398 labelled images, 797 boxes) at IoU 0.50, and coverage on the restored videos.
- Dataset is partially labelled: 19 images carry a `Person` body box while 194 carry a person-related annotation, so box-level precision is reported both strictly and against person-presence with containment matching.
- Person: box precision 0.7797 / recall 0.6173; **image-level presence precision 0.8721 / recall 0.9845**.
- Heavy machinery: box precision 0.7255 / recall 0.5086; **image-level presence precision 0.9673 / recall 0.7437**.
- Per-class recall: `Person` 0.9275, `Hardhat` 0.9312, `wheel loader` 0.7683, `dump truck` 0.6495, **`EXCAVATORS` 0.2000**.
- Root cause of the machinery gap: both bundled weights are COCO-80 models with no excavator class. Calibration is not the limiter; the weights are.
- Video validation is coverage-only because the recordings have no ground truth: 8 of 9 videos yield worker detections, 3 of 9 yield heavy-equipment detections, median inference 60 ms on CPU.
- `src/api/media_detection.py` serves the calibrated overlay, resolved against the installation root, and degrades to no boxes plus an explicit reason when weights are unavailable. 18 calibration/overlay regression tests added.
- Verified live on both instances: identical calibrated packets, zero scale error, all boxes within frame bounds.

## Playback flow of restored recordings — 2026-09-29

- Root cause of the low flow was measured, not guessed: a per-request `POS_FRAMES` seek into the inter-frame-coded MP4 costs 79.1 ms/frame (12.6 fps ceiling) while sequential decode costs 2.2 ms/frame (445 fps), and the frame was being read twice per request (once for the JPEG, once for detection) plus ~60 ms of inference.
- `media.FrameSource` keeps a cursor, reads forward and seeks only on a jump: forward decode measured 1.14 ms/frame (877 fps). Backward scrubbing and random access still work through a seek.
- `hub.original_frame` decodes once and shares the frame between the encoder and the detector.
- `media_detection._Prewarmer` warms the clip on a background thread with its own capture handle, sustaining ~18 detections/s on this CPU; a 240-frame clip is ready in ~13 s and instant on replay.
- In-process serve path improved from 202.2 ms/frame (4.9 fps) to 3.1 ms/frame (321 fps), a 64.9x reduction in per-frame cost.
- Over HTTP, both instances went from ~9-10 fps cold to 123-136 fps prewarmed (13.0x Docker, 13.8x source).
- **Precision is unchanged**: every frame is detected individually, nothing is interpolated, repeated or subsampled. A test asserts each frame reaches the detector and that a prewarmed result is exactly equal to an on-demand result.
- A 24 fps clip uses ~7% of the frame budget at 1x and ~30% at 4x, so the player now offers 4x and 8x speeds and displays the measured `fps served`.
- Bug found and fixed while testing: `get_detector()` memoised a load failure permanently, so a single "weights not available" moment disabled the overlay for the whole process lifetime. The success is now cached against the resolved weights path and failures are never memoised; a missing file that later appears is picked up automatically.
- Two test defects were also caught and fixed rather than papered over: a backward-read test zipped reversed frames in the wrong order, and a precision test depended on which weights happened to be present (now driven through a stub detector).
- Regression: 150 Python tests passed (11 new playback tests); 17 browser tests passed; container smoke passed.

## 1080p30 presentation, de-duplication and stall removal — 2026-09-29

- **Stall cause found by measurement, not guesswork.** Playback had three separate bottlenecks, each fixed at its source:
  1. At 30 fps the consumer needs 30 frames/s but a single prewarm worker sustained only ~18 detections/s, so the playhead outran the producer and playback fell back to the ~60 ms inline path (~5 fps).
  2. Four workers still measured 17/s because they shared one YOLO instance. Giving each worker its own model raised it to **43.6 detections/s** (2 threads each; 16 cores available).
  3. The loop wrap rewound the frame cursor and forced an MP4 seek of ~70 ms, stalling every loop. Frames are now pre-encoded during warm-up, so a wrapped loop is served from cache with no decode and no encode.
- Prewarm workers are built sequentially up front: constructing several YOLO instances concurrently was silently dropping the tail of a worker's range, and the progress counter was being advanced *before* the result was stored, over-reporting readiness. Readiness is now only reported once a result is really cached.
- A global cap (`SENTINEL_MEDIA_PREWARM_CLIPS`, default 1) stops several clips warming at once, and the clip the viewer just selected evicts the previous one. Touching all 14 recordings used to start 14x4 workers and pushed the serve path from ~6 ms to ~28 ms median; with the cap the watched clip warms in 5.8 s and plays at 0.39 ms median.
- **Every recording is presented as 1920x1080 @ a fixed 30 fps.** Sources at 720p, 1080p, 4K, portrait and 362x398, and 20/23.98/24/29.97 fps, are scaled to fit and letterboxed onto one 1920x1080 canvas (aspect preserved, never stretched; the letterbox offsets are reported). A 30 fps output index maps to a real source frame, so no synthetic intermediate frame is invented; a 24 fps source is presented by holding frames and shares one genuine detection per source frame.
- **Duplicates removed from the list.** Three recordings existed twice (identical LFS OIDs: `Worker_in_excavator_blind_spot`, `Worker_near_heavy_equipment_exca9`/`exca_near_miss`, `30sec_construction`/`real_site_active`). The UI now lists 14 unique recordings; the `real_videos` copy wins, the other stays addressable, and the alias is reported so no provenance is lost.
- Detection cache raised from 384 to 1600 entries so a 901-frame recording no longer evicts its own warm results and drops to inline inference mid-playback. JPEG cache is now bounded by bytes rather than entries.
- Verified live on both instances: 14 recordings listed, 0 duplicates, all advertised 1920x1080 @ 30 fps, every served frame 1920x1080. Playback over the full 30 fps timeline plus a loop wrap: Docker median 5.93 ms (169 fps), worst 8.6 ms; source median 4.74 ms (211 fps), worst 7.8 ms - at most 26% of the 33.3 ms budget, so no frame can stall playback.
- The player control bar now shows the presentation format (e.g. "30 fps · 1920×1080") from the catalog rather than a hard-coded string.
- Regression: 164 Python tests passed (14 new tests locking in 1080p30, de-duplication and stall-free playback); 17 browser tests passed.

## Sprint validation framework adopted — 2026-09-29

- The DeepSeek Harness sprint-validation brief is now a standing procedure, documented in `deploy/VALIDATION_FRAMEWORK.md` and enforced by `scripts/sprint_validation.py`.
- The harness encodes the verdict vocabulary (PASS / FAIL / BLOCKED / NOT TESTED), the four evidence classes, the 100-item registry across the 12 cards, the four-column tally, and the rule that a card is only complete when all of its items pass.
- Its central safety rule is mechanical: an item requiring physical-site evidence cannot be closed with a recording, a disposable RTSP fixture or a public dataset. Verified by negative control — marking a site-gated item PASS with `DEMO/RECORDING` evidence is rejected as a policy violation.
- Validated on the current sprint: `deploy/validation/DEEPSEEK_HARNESS_SEC01_12_RESULTS.md` records 100 item verdicts (35 PASS, 26 FAIL, 35 BLOCKED, 4 NOT TESTED) with per-item method, observed behaviour and a reproducible evidence reference, plus a prioritized gap list and the site inputs required to unblock each card.
- The SEC-09 items 5 and 6 duplication in the source card is retained and evaluated separately, as the brief requires.
- Source checkboxes were not edited, and the run was verified to have changed no application code: all 17 application source hashes match the pre-validation baseline.

## Hazard zone, structured safety event and 3D state work — 2026-09-29

Implemented to close the FAIL items recorded in `deploy/validation/DEEPSEEK_HARNESS_SEC01_12_RESULTS.md`.

- **Zone on the camera image** (SEC-04.3, SEC-08.2). `HomographyProjector.metric_polygon_to_pixels` projects a metric ground polygon back into frame pixels, densifying edges, rejecting polygons with fewer than three distinct vertices, keeping the visible run of a zone that leaves the frame, and returning `None` when the zone is not visible at all. The camera panel now paints the zone, its name, and a colour that follows the business state.
- **Entry, continued presence and exit** (SEC-04.5, SEC-05.4-5.6). `src/edge/zone_occupancy.py` tracks occupancy per (zone, agent). Entry fires once; continued presence is reported on a 1 s cadence so the rate measures time inside the zone rather than the frame rate; exit carries `dwell_seconds` and the peak level reached. A track lost for more than 3 s closes occupancy as `track_lost`, so occupancy cannot latch when a stream stalls, and a one-frame dropout is not reported as an exit.
- **Structured safety event** (SEC-07.1-7.9). `src/schemas/safety_event.py` defines a versioned contract carrying the source camera, site, zone, machine, both timestamps, the risk level, the business state, the event type, and a `detection_status` that distinguishes a measured detection from an illustrative one. Events are emitted **automatically** by the runtime on a zone transition or a debounced risk change, written to `data/incidents/events-YYYYMMDD.jsonl` and broadcast on the same WebSocket. The sink is best-effort: a read-only filesystem or a failing subscriber never takes the inference loop down or loses the event.
- **Documented risk mapping** (SEC-06.8, SEC-12.5, SEC-12.6). `deploy/SAFETY_EVENT_CONTRACT.md` and `deploy/RISK_LEVELS.md` state the four-level to three-state mapping, the condition behind each state, the debounce, and the zone rules. An unknown level maps to `SAFE` rather than inventing a risk.
- **Machine identity and timestamp on the alert** (SEC-08.5, SEC-08.6). The panel shows the machine identifier, the zone, the business state with its colour, and a timestamp.
- **3D machine and state** (SEC-10.6, SEC-11.2, SEC-11.5, SEC-11.6). The twin now builds a simplified machine body at the zone centroid, labelled with its `machine_id`, and both the machine and the zone are coloured by the business state, so the Safe and Risk states are visibly distinct in the scene.
- **Honesty preserved.** A restored recording has no measured calibration, so its zone is an example projection: it is reported only for media that match a scenario, always with `calibrated: false`, and the panel prints `ILLUSTRATIVE ZONE - NOT REGISTERED TO THIS CAMERA` **on the image itself**, not only in a badge. Media without a matching scenario get no zone at all rather than an invented one. Events for those frames carry `detection_status: ILLUSTRATIVE`; only a live camera with a measured homography and a signed manifest produces `MEASURED`.
- Two defects found by the new tests and fixed: severity levels were compared as strings (`"NORMAL_LEVEL_0" > "CRITICAL_LEVEL_3"` is true alphabetically), and a risk-transition event read a stale member variable instead of the pairs from the frame that caused it.
- Regression: **202 Python tests** (up from 164; 38 new in `tests/test_safety_zone_and_event.py`) and **28 browser tests** (up from 17; 11 new in `tests/safety_zone_ui.test.cjs`) pass. Container smoke exits 0.
- A third defect was found by the working-directory regression test: the example calibration was resolved relative to the process cwd, so the source app (started from `.source-runtime`) silently showed no zone. The path is now resolved from the repository root, and a missing calibration is reported once through the logger instead of looking like "no zone configured".
- Verified live on both instances: the frame packet carries `zone_overlays` (88 pixel vertices), `machine_id=MACHINE-EXCAVATOR`, `zone_id=ENV_EXCAVATOR_BLIND_SPOT`, `business_state`, `camera_id` and `detection_status`, and the panel exposes the machine, zone, state and timestamp elements. The four-level to three-state mapping is declared and served: `NORMAL->SAFE`, `ADVISORY->ATTENTION`, `WARNING->RISK`, `CRITICAL->RISK`.

## UI resolution limit: nothing above 1080p is listed — 2026-09-29

- The UI no longer shows any recording larger than 1080p. Two files were withheld: `14117669-uhd_3840_2160_30fps.mp4` (3840x2160) and `15100676_2160_3840_30fps.mp4` (2160x3840).
- The rule is applied on the frame's long side (`max(width, height) <= 1920`), so an ordinary portrait 720p recording (720x1280) and an exact 1080p recording (1920x1080) are kept; "more than 1080p" excludes 1080p itself. A recording whose source geometry could not be measured is kept, because absence of a measurement is not evidence that it exceeds the limit.
- The filter lives in `media.unique_catalog()`, which every UI surface reads, so one change covers the scenario dropdown, the restored-assets panel and the displayed counts. Verified on both instances: dropdown 12 entries, restored-assets panel 13 entries, **0 above 1080p**, and the summary counts agree (13 listed of 15 indexed).
- The files were withheld from the UI first, then **deleted from disk** at the project owner's explicit instruction. Removed: `data/real_videos/14117669-uhd_3840_2160_30fps.mp4` (87,182,348 bytes) and `data/real_videos/15100676_2160_3840_30fps.mp4` (255,720,342 bytes), about 328 MB. Each file existed in one place; no copies remain.
- The removal is **recorded, not silent**. `data/original_assets_manifest.json` gained a `removed_objects` section with each path, its upstream LFS SHA-256, size, the reason, the date, and the exact `git lfs fetch` command to restore it against the pinned upstream commit. The upstream `lfs_objects` list is untouched, so the manifest still documents the full upstream asset set.
- `scripts/verify_original_assets.py` now distinguishes a recorded removal from a loss: a removed object is reported as `LFS REMOVED ... absent as intended` with its reason, and only an *unrecorded* missing file is an error. The verifier exits 0 with 18 objects verified and 2 recorded removals.
- Tests enforce that the escape hatch cannot be used quietly: a removal must carry a reason, a date, the upstream OID and a restore command, the file must actually be absent, and a removed recording must not reappear in the UI or as an API scenario. The manifest-driven assertions in `test_original_assets.py` and `test_demo_api.py` now derive the removed set from the manifest instead of hard-coding it.
- The Docker image was rebuilt: `sentinelzone-ai:demo-media` shrank from 3.77 GB to 3.09 GB and no longer contains either file. Both instances now answer `404` for those media ids, so the recordings are not merely hidden but gone.
- Regression: **212 Python tests** and **28 browser tests** pass; container smoke exits 0.
- Nothing was committed, per the standing instruction. `git status` shows the two paths as deleted, so the change is visible and reviewable in the working tree.

## Audit: was anything below 1080p removed? — 2026-09-29

The rule was restated as "1080p at maximum, no minimum, and never a copy", and every media file was audited against it.

- **Nothing eligible was removed, so nothing needed restoring.** All 13 unique recordings on disk are listed. The only two files not in the UI are `exca_near_miss.mp4` and `real_site_active.mp4`, which are byte-identical copies of files that *are* listed, so their absence is exactly what the copy rule requires.
- **No minimum is imposed.** The listed library spans 362x398 (`veo3_construction`) up to 1920x1080, and a recording whose geometry cannot be measured (`construction_ppe_sample.mp4`) is kept rather than assumed oversized. The rule is compared on the frame's long side, so portrait 720p is included.
- **The three hidden duplicates are the copy rule working, not data loss:** `real_site_active.mp4` is identical to `30sec_construction.mp4`, `exca_near_miss.mp4` is identical to `Worker_near_heavy_equipment_exca9.mp4`, and the second `Worker_in_excavator_blind_spot.mp4` is identical to the first.
- **Curated demo bundles remain unlisted by an earlier, still-standing instruction.** `src/demo_assets` holds 240 JPEG frames at 960x540 across `blind_spot`, `near_miss` and `barrier`. They are below 1080p and not byte-identical to any listed file, but they are re-encoded stills taken from the very videos that are listed, and they were unlisted at the project owner's earlier request to keep only the original media. They were therefore **not** restored, because doing so would reverse that instruction and re-add 540p duplicates of media the UI already presents at 1080p.
- **Two dataset images exceed 1080p and were deliberately left alone:** `image_257_jpg.rf.8040d2ab....jpg` (2046x568) and `construction-9-_jpg.rf.a09651a4....jpg` (2000x1333), both in `data/roboflow_downloaded`. These are labelled training/validation images, not UI media. Deleting them would break the dataset image/label parity that the detector evaluation depends on, so they are reported here rather than removed. Removing them needs an explicit decision.
- Regression: **214 Python tests** (2 new, asserting there is no minimum resolution and that the UI lists no copy) and **28 browser tests** pass.

## Original settings audited and aligned — 2026-09-29

Recorded in full in `deploy/SETTINGS_ALIGNMENT.md`.

- **The original settings were never changed, so nothing needed restoring.** `config/default_config.json`, all five `data/manifests/*.json`, and `data/roboflow_downloaded/data.yaml` are byte-for-byte identical to the original repository at `bc8e2c88d376575503e77351f1b123b13c547535`. The original camera (`CAM-02-MAST`), site (`SITE-5-EARTHMOVING`), homography and risk thresholds (`warning_ttc` 2.5 s, `critical_ttc` 1.5 s, `prob_threshold` 0.65) are asserted unchanged by `tests/test_settings_alignment.py`.
- Three deliberate deviations remain, each documented: the dataset `path` made portable from an unresolvable Windows absolute path, dependencies moved to a pinned lock file, and the two 4K recordings removed at the owner's instruction.
- **Inconsistency found and fixed in code: a live camera had no zones at all.** The original live path loaded envelopes only when `manifest.site_id == config.site_id`, which the original repository never satisfies (config says `SITE-5-EARTHMOVING`, the active manifest is `SITE-EAST`, the `SITE_5` manifest is spelled differently). The result was zero envelopes, so no zone overlay, no entry/exit events and no zone risk adjustment. The settings were left untouched and `src/edge/manifest_selector.py` resolves the manifest tolerantly (exact, then normalised, then active fallback), reporting which matched. The runtime now adopts the manifest's site, so an event's `site_id` always agrees with the manifest its `zone_id` came from, and `site_identity()` is published on the frame packet.
- **Inconsistency that code cannot fix: the zone geometry.** All five original manifests place their envelope at metric x[100,125] y[50,70], while the original `CAM-02-MAST` homography sees roughly x[-5,3] y[0,10] in a 1920x1080 frame; the zones project to image x[2578,3706], beyond the right edge. No zone is drawn on the image because the projector refuses to draw a zone it cannot see. Metric-space behaviour is unaffected. Closing this needs the real measured calibration and the real site zone coordinates - site data, not a code change.
- Two defects were caught while doing this: the manifest path was resolved relative to the process working directory, so the selector silently returned nothing when the app started from `.source-runtime`; and the settings test asserted a manifest filename that does not exist. Both fixed, the first now covered by a test that runs from a different directory.
- Regression: **231 Python tests** and **28 browser tests** pass; container smoke exits 0; original asset verification exits 0 with 18 objects verified and 2 recorded removals.

## Zone overlay removed, 3D canvas restored to the original — 2026-09-29

At the project owner's request the zone overlay and the fabricated machine box were removed and the 3D canvas was returned to the original repository's implementation.

- **Removed**: the on-image zone polygon and its `ILLUSTRATIVE ZONE` warning, the zone and machine badges, and the `zone_overlays` / `zone_id` / `machine_id` packet fields. The only polygon left in the application is the original repository's own BIM trench rectangle, `x[-8,12] y[3,10]`, taken from the original twin's `PlaneGeometry(20,7)` at `(2, 6.5)` and read from one constant so the 3D plane and the occupancy check cannot diverge.
- **Restored verbatim from the original**: the legend (`Metric Ground Plane Twin (EPSG:3857 Geodetic Datum)`, `BLUE: Worker Tracks`, `AMBER: Heavy Machinery`, `RED: BIM Trench Hazard`), the 50 m grid, the trench plane, the worker disc and post, the machinery hull and cab, the two path lines, the conflict ring, and the original `tracks_3d` contract the twin consumes.
- **Workers and machinery now share one ground plane.** Restored recordings previously sent no tracks at all, so the twin had nothing to draw and the only visible object was an amber box parked at the zone centroid, a made-up location. `src/api/media_tracks.py` now derives metric positions from real detections.
- **Why the ground plane had to be fitted per recording.** The original example calibration cannot place the foreground worker at all: its horizon sits at row 760 of a 1080-row frame, so the bottom third of the frame is *behind* the camera plane and a footpoint there projects to negative depth. Rescaling that matrix cannot repair a negative depth, so the plane is fitted: the original focal length and principal point are kept and only the horizon and camera height are fitted, spanning 1.5 m at row 0.95 to 30 m at row 0.25. A footpoint above the fitted horizon is clamped to the far band rather than dropped, so distant machinery always appears. This brings the worker and the nearest machine to about 29 m, against about 93 m with the original calibration.
- **Added to the worker frame**: a per-worker note of `IN ZONE`, `IN ZONE - MOVING`, `MOVING` or `STATIONARY` in the original tag style, an event log of `ZONE_ENTRY` / `ZONE_OCCUPANCY` / `ZONE_EXIT` with dwell time, and the original conflict ring pulsing at the point of exit. These four labels are the only new strings in this work, because the original application had no in-zone label.
- **Defects found and fixed while doing this**: occupancy state was inferred from the event log, so `ZONE_EXIT` re-fired on every subsequent frame; the draw window was reused for occupancy liveness, so a single skipped frame closed a zone; speed was instantaneous, so one jittery frame on a distant machine read as `MOVING`; a far clamp used the horizon instead of the far band, putting machinery at 120 m; and three separate edits to the twin deleted the global `resize`, `const twin=createTwin()` and everything after `createTwin`, which the browser tests caught. A new test also leaked `SENTINEL_MEDIA_PREWARM=0` into every later test in the process, which silently disabled prewarming.
- **Validation re-opened honestly.** `SEC-04.3` and `SEC-08.2` return to FAIL because the on-image zone overlay was removed; `SEC-11.5`, `SEC-11.6` and `SEC-10.6` return to FAIL because the state-driven colouring was removed with it. `SEC-11.2` now passes on the original agent meshes. `SEC-05.4-5.6` stay PASS: entry, continued presence and exit are retained, now measured against the canonical trench. The report was regenerated through the harness in strict mode.
- Regression: **272 Python tests** and **28 browser tests** pass; container smoke exits 0.

## Proximity grading and PPE compliance — 2026-09-29

- **Proximity ladder.** `DANGER ≤ 3.3 m` is the engine's own collision radius for a worker/machinery pair, taken from the same footprint radii the edge runtime sets, and a test reads the engine's expression so the two cannot diverge. `NEAR ≤ 8.0 m` is an **engineering default with no site validation behind it** and must be re-derived from site data before it is relied on. It is deliberately not treated as a validated threshold anywhere in the report.
- **PPE is a colour estimate, not detection.** The playback detector is COCO-80 and has no hardhat or vest class. Compliance comes from the original repository's HSV head/torso rule. It was not validated on this data: it has no measured accuracy, no confusion matrix, and no comparison against a PPE-annotated ground truth. Any card that asks for PPE detection accuracy remains **BLOCKED**, not PASS.
- **`UNKNOWN` is a real state.** A worker below the 20 × 10 px crop threshold is reported unmeasured and rendered `UNKNOWN`. No card may count an unmeasured worker as a missing hardhat.
- **Proximity is a measurement of the fitted plane, so it inherits that plane's error.** The ground plane is fitted per recording from the original focal length and principal point; the distances quoted here are only as good as that fit, and they are not georeferenced.
- **Measured on the restored recordings**: the cab operator on `real_videos_Worker_and_excavator_near_barrier` reads `DANGER` at 0.18 m with `hardhat=OK, vest=NO VEST` measured; frame 250 reads `NEAR` at 4.14 m; the blind-spot clip reads `SAFE` at ~28 m. Served-frame latency median 6.9 ms, p95 8.8 ms, against a 33.3 ms budget.
- **Defects found and fixed in this work**: `ZONE_EXIT` re-fired on every frame because occupancy was inferred from the event log rather than held as state; the draw window was reused for occupancy liveness, so one skipped frame closed a zone; and scrubbing to an earlier frame that had no stored snapshot returned the *newest* snapshot, whose track ids were then zipped onto the scrubbed frame's boxes, putting one person's state and grade on another's box. That last one now reports nothing rather than the wrong thing.

## 3D orientation, separation, and polygon calibration — 2026-09-29

- **The 3D orientation defect was measured, not eyeballed**: across the blind-spot clip, stationary machinery carried a measured heading spread of **286 degrees**, because the heading is derived from footpoint pixel jitter. The fix gates the *rendered pose* on the agent actually moving. The measured `heading` is unchanged and still reported, so the noise is still visible to any consumer that wants it.
- **The trench band is fitted, so `IN ZONE` is unvalidated as a hazard signal.** Shape and lateral placement are the original repository's; only the depth band is derived. Coverage of observed workers rises from 6/32 to 21–23/30–32, which makes the polygon sit on the work rather than behind it. It does **not** make it a surveyed hazard boundary, and no card is upgraded on that basis.
- **Separation was achieved without moving a person.** The machinery hull is offset forward by half its 5.0 m length so it stops covering the ground its own footpoint stands on. Worker metric positions and the reported proximity distance are untouched, so no distance is manufactured to make the picture look better.
- **Left in place by explicit request**: `test_videos_crew_site_video` still appears in the UI. Its detection is known-broken — **0 of 74 agent-frames** found machinery on footage that visibly contains an excavator and a tower crane, so every worker on it reads `NO MACHINERY IN FRAME` and its 3D view has no plant. This is recorded here rather than hidden.
- **The UI funnel is unchanged.** 15 of 16 clips are not 1080p at the source (most are 1280x720); the presentation layer upscales every clip to 1920x1080@30, so all 12 visible clips pass the funnel. Applying a strict source-resolution rule would have left one clip.
- **Detection boxes needed no change**: 0 of 66 observed boxes fall outside the 1920x1080 frame, so the detection frames were already calibrated and were left alone.
- Steady-state served-frame latency after a warm-up: median **6.7 ms**, p95 7.6 ms, against the 33.3 ms budget. A cold-cache first walk is not a steady-state number and is excluded.

## Overlay annotation collisions — 2026-09-30

- The collisions in the review screenshots were **measured, not eyeballed**: replaying the overlay layout over 86 live frames of the two clips gave 55 label-on-label overlaps, 34 chip-on-chip overlaps, 6 label-over-chip overlaps, and 131 panels clipped outside the drawn image. After the packer, all four counts are **zero**.
- This is presentation only. No detection, no metric position, no distance, no zone state and no PPE reading was changed, so nothing measured can move because of it.
- **Deliberately left alone, at the project owner's direction**: the 3D workers still merge into one blob when they stand 0.6–1.2 m apart, and the red trench is still 20 m wide while the work in that clip spans about 2.9 m. Both are accurate renderings of measured positions, and shrinking the trench would reverse the calibration done the day before, which kept the original repository's 20 x 7 shape.
- **Also left alone**: the Edge Cycle Latency card. It shows the raw detection time on one CPU thread (150–245 ms observed), which is honest; the served frame is about 6.7 ms because detections are cached. Relabelling it was not in scope for this change.
- Regression: **305 Python tests and 32 browser tests** pass, including six new browser tests that replay a crowded seven-worker frame through the real packer. Container smoke exits 0.

## Ground plane, polygons, trails and ghosts — 2026-10-01

- **The absolute metric scale was wrong and is now anchored on measurement.** The old fit produced a 0.84 m camera height — below standing eye level for what are visibly mast cameras — and people **0.17 m** wide. The anchored fit gives 2.6–5.4 m camera heights and person widths of 0.46–0.83 m. This is the single largest correction in the project: every distance, the proximity grades and the zone band inherit it.
- **The anchor is an assumption, stated as one.** `NOMINAL_SHOULDER_METRES = 0.5` is a reference adult shoulder width, not a site survey. It is a documented constant with a plausibility guard, and a site survey would replace it. **No card asserting a true metric accuracy can PASS on this basis.**
- **The fit is deterministic per recording**, verified by requesting the same clip from frames 5, 60 and 120 and getting an identical camera height. That matters because a playhead-dependent plane would let a scrub move the ground under a stored snapshot.
- **The proximity ladder was re-measured on the corrected scale**: DANGER 7.5 %, NEAR 21.7 %, SAFE 20.5 %, NO_MACHINE 50.3 % of 161 worker states. It discriminates, where before the miscalibrated scale compressed everything. The `DANGER` radius is still the engine's own `r_col` and the `NEAR` band is still an unvalidated default.
- **Trails were genuinely broken**: two shared lines meant only the last-drawn agent had a path. Now one line per track, verified in the browser suite that two agents produce two distinct trail geometries.
- **Ghosts measured and removed**: machine boxes fell from 253 to 195 across a comparable sample as fragments merged; 8 cab operators are marked across 206 frames, **0** of them leak into the 3D agents, and **0** workers nested more than 80 % inside a machine remain unflagged. No person is ever deleted.
- **Known and left in place**: a light vehicle or plant box nested more than 80 % inside a heavy-equipment box (37 cases) is not merged, because the two are different classes and merging them would risk removing a real object. This is a class-assignment limitation of the COCO mapping, not a tracking fault.
- **Defect found while doing this**: skipping an operator shortened the tracker's measured list, so the `detection_track_ids` array — indexed by position in that list — misaligned against the original boxes and attached one person's state and footprint to another's box. Track ids are now indexed by the *input* detection.
- Regression: **329 Python tests and 55 browser tests** pass, including 24 new tests for calibration, footprints and ghosts, and 7 new browser tests replaying the real update loop. Container smoke exits 0. Warm served-frame latency median 7.0 ms, p95 7.8 ms.

## Regression: the twin threw on every frame — 2026-10-01

**Symptom.** Selecting a clip left the camera panel blank with
`trenchMesh is not defined` in place of the frame, and the header read
`NOT CONNECTED` / `UNAVAILABLE` / `NO OBSERVATIONS`. The 3D canvas still drew,
because its scene had been built before the throw.

**Cause.** `trenchMesh` was declared `const` **inside** the `try` block that
builds the scene, but `update()` — which positions and scales it from
`packet.zone` — is declared after that block. The binding was block-scoped, so
every call to `update()` threw a `ReferenceError`, which propagated out of
`display()` and left the panel unpainted.

**Why the tests missed it.** The update loop was exercised by extracting its
source and calling it through `new Function('…','trenchMesh', …)`. The test
supplied `trenchMesh` as a *parameter*, so the real closure was never consulted
and the scoping error was structurally invisible. That is the same failure mode
as the earlier two `new Function` traps in this project: a harness that is more
convenient than the real thing.

**Fix and guard.** `trenchMesh` is now hoisted beside `conflictRing` in the
outer declarations and assigned inside the `try`. A new browser test builds the
**real** module in a sandbox, calls the real `createTwin()`, and drives
`update()` without injecting names, so a name the function cannot see fails the
test. Reintroducing the bug was confirmed to fail exactly that test and no other.

Regression: **329 Python tests and 56 browser tests** pass. Container smoke
exits 0. The clip from the report now serves detections, tracks and polygons as
normal.

## Pose snapping fixed, and a measured judgement call left open — 2026-10-01

**Symptom.** With the polygon work in place, the 3D view showed the person
polygons as scattered plates at unrelated angles.

**Measured cause.** The rendered pose followed the raw measured heading. Across
33 sampled frames of `10810477`, the per-frame swing of that heading reaches
**177.4°** on a single frame — the largest of 794 transitions. One bad frame,
whether a mis-association or a footpoint that lands across the body, is enough
to throw a polygon across the scene. The underlying defect is in the heading
measurement's 5-point window, not in the drawing.

**Fix.** The rendered angle eases toward the target (`POSE_EASE = 0.18`) and the
turn is capped per frame (`POSE_MAX_STEP ≈ 10.3°`), so a snap becomes a turn.
Worst per-frame swing over the same 794 transitions falls from **177.4° to
10.3°**. The pose still follows the heading only while the agent is moving, so a
stationary worker's footpoint noise cannot turn them — the gate the original code
had and that was accidentally dropped in the first attempt at this fix. **The
measured heading on the packet is untouched**; this is a rendering decision only,
and the reading in the panel is unchanged.

**Left open deliberately: nested worker boxes.** In the reported frame, 1 of 7
worker boxes is 65 % inside another (0.34 confidence inside a 0.88 box), and
across 33 frames **13 of 178 worker boxes (7.3 %)** nest more than 70 % inside
another worker box. These look like the same person detected twice.

They are **not** suppressed, and that is a safety judgement, not an oversight: a
box deeply inside another person's box can be a second worker in a crowd, a
crouching worker, or someone partly occluded, and removing one would mean a
person vanishes from the panel. The ghost suppression therefore continues to
never touch a `WORKER`. Suppressing only when the contained box is *also* far
less confident is defensible on this footage, where the workers are adults at
similar scale — but it is the user's call, not a silent default.

## Scrambled polygons and the framing — 2026-10-01

**Measured cause of the scramble.** The person polygons were not scrambled by
the rendering; they were speckle mixed with real agents. `WORKER` boxes as small
as **21x55 to 29x65 px** are detected — a hardhat, a hi-vis panel, a body cut off
by the frame. Each was given a footpoint at whatever depth its bottom row
implied and turned into a polygon of 0.25 m lying at an arbitrary distance among
the real workers. That produced tracks of 0.25 m beside tracks of 1.30 m for the
same kind of object, and the canvas read as scattered plates.

Two filters now keep fragments off the ground plane, and **both leave the box on
the camera frame with its PPE notes** — exactly as an operator's does:

- `MIN_PERSON_BOX_HEIGHT_PX = 70`. Below that the box cannot be a whole body.
- `MIN_PLAUSIBLE_PERSON_WIDTH_M = 0.35` — a measured width, not a pixel count.
  Shoulder width is roughly constant however a person is turned, so 0.35 m is
  about 70 % of `NOMINAL_SHOULDER_METRES`; below it there is no whole body behind
  the box.

The width test used to be a **clamp**, which made a fragment *bigger*. Clamping a
speck up to a minimum is what put it on the ground plane in the first place.

**Defect found while doing this.** Applied to the fallback plane, the width test
rejected **every** person: `30sec_construction` went from 4 agents to 0, and
`veo3_construction` from 1 to 0. Those two clips fail the calibration scan and use
the assumed band, which solves to a 0.84 m camera under which everybody measures
0.17 m wide. Absolute measurements are only meaningful on an anchored plane, so
`GroundPlane` now carries `anchored` and the test is gated on it. Both clips keep
their agents again.

**Framing.** The camera sat a fixed **42 m** back over a 50 m grid while the
workers occupy about **6 x 4 m**. Most of the view was empty grid with a small
cluster in the middle — the pollution. The view now frames what is on the ground
plane: it eases the target to the agents' centre and pulls the radius in to
`max(8, cluster radius x 2.4)`. On the reported clip that settles at **8.7 m**
instead of 42 m, so the agents and the trench fill the frame. A wheel-zoom by the
user is respected from then on (`framedByUser`). The trench, the grid, the ring
and the post are all still the original repository's geometry.

The view state moved from loose `target`/`radius` variables into one `view`
object, so it has a single owner and can be observed in tests. That change also
caught a wheel handler still assigning to a bare `radius` — a leaked global, so
the zoom would never have worked.

**Regression:** 335 Python tests and 60 browser tests pass, including 6 fragment
tests and 4 framing tests. Container smoke exits 0.

## The marker was a fallen post, and the trail was poisoned — 2026-10-01

Two separate defects, both of which had to go before the person marker looked
like a straight polygon with a trail.

### The post was lying on the ground

The original twin stood the worker post upright with

```js
if(human) body.rotation.x = Math.PI/2;
body.position.z = .9;
```

Rewriting the marker dropped both lines and set `position.z = 0.35`. A
`CylinderGeometry`'s axis is **Y** and this scene's up is **Z**, so the 1.8 m post
was lying flat on the ground — a long bar with rounded ends, which is what made
the marker read as a blob rather than a straight polygon with a direction. Both
lines are restored, verified in the real module: `rotation.x = 1.5708` (90.0°),
`position.z = 0.9`.

### The trail and the path could not exist

The original drew **two** lines per agent: `history` in grey `0x64748b` (the
trail behind) and `forecast_trajectory` in `0x38bdf8` / `0xfbbf24` (the path
ahead). In the restored recordings the forecast was served as a hard-coded empty
list, so the "path" the legend promises was never drawn at all.

Worse, the trail itself was poisoned. `MediaTracker.step` advanced over every
frame between the last one and the one requested, filling each skipped frame with
`None` detections:

```python
boxes = detections if step_index == index else None
```

and **cached** that empty snapshot. Because the tracker never rewinds, every
frame before whichever one the client requested first was permanently empty:
asking for frame 0 after frame 55 returned **no agents at all**. The trail and
path therefore existed only for frames the user happened to play through in
strict order from the start — which is why scrubbing back blanked the panel and
why the lines were missing.

Three changes:

1. `MediaTracker.forecast()` projects the measured velocity over
   `FORECAST_HORIZON_SECONDS` (3 s, 6 points) and is emitted **only while the
   agent is moving**, because a stationary agent's velocity is footpoint noise
   and a path drawn from it would invent a direction.
2. `step(..., frames_at=...)` fills a skipped frame with **its own detections**
   instead of `None`, so every frame in a jump is stepped properly and its
   snapshot is real. The hub supplies the callback, which reads the same
   detection cache the current frame already used, so the catch-up costs no extra
   inference after prewarm.
3. The client draws both lines per agent, in the original colours, at `z=0.15` —
   above the trench plane (0.05) and the footprint (0.02), so neither hides the
   other.

Verified on the live app, asked deliberately out of order: frame 0 gave 4 agents
with a 1-point trail, frame 20 gave 14-21 points, frame 100 gave 30 points and
frame 55 gave 30 points with a 6-point path. Every response under 12 ms once warm. A cold jump
that skips 130 frames takes about 9 s once, then is instant.

**One behaviour change worth knowing:** a track now outlives its last detection
for `TRACK_DROP_SECONDS`, so `tracks_3d` can hold slightly more agents than the
current frame has boxes. That is deliberate — it is what keeps an agent on the
ground plane through a frame where the detector missed them — and one stale test
assertion was corrected for it.

**Regression:** 343 Python tests and 63 browser tests pass, including 4 new
random-access tests and 5 new marker/line tests. Reverting either fix was
confirmed to fail those tests and no others. Container smoke exits 0.

## The proxies were oversized and the paths shot off screen — 2026-10-01

With the trail and path finally drawing, two sizing faults became obvious: a few
enormous shapes filled the view, and long straight lines crossed it.

### The polygons were the size of the detection box, not of the plant

The measured footprint comes from the box, and the detector sometimes returns a
machine box spanning almost the whole frame — on the blind spot clip, x 85..1893
of 1920. The old caps let that through: hulls were drawn **4.2-5.9 m wide and
8.1-11.3 m long**, and people up to the 1.30 m person cap. A tracked excavator is
about 3 m wide.

The caps are now the physical size of the object:

| | before | now |
|---|---|---|
| machinery width | 1.2 – **7.0 m** | **2.0 – 3.6 m** |
| person width | 0.25 – **1.30 m** | 0.25 – **0.75 m** |

Drawn footprints went from 4.2-5.9 × 8.1-11.3 m to **3.1-3.6 × 5.9-6.9 m**, and
people from 0.45-1.30 m to **0.28-0.75 m**. The depth ratio stays the original
5.0 : 2.6, and the bounds still contain the original's 2.6 × 5.0 m excavator.

The post's diameter now follows the measured width too, so a small person no
longer carries a post wider than they are. Its 1.8 m height is unchanged.

### The framing had overshot

Framing fixed at 42 m left the agents as specks; the first correction closed to
an 8 m floor, which was too near — the enlarged shapes filled the whole panel.
The view now also allows for **each object's own footprint**, not just the spread
of the centres, and the floor is **16 m**. It settles at 16 m on these clips,
recentred on the action, against a physical maximum object length of 6.9 m.

### The path was projected from noise

`forecast()` used the instantaneous velocity. On the 30sec clip that reached
**10 m/s** for a worker whose smoothed speed was 3.2 m/s, drawing paths
**27-51 m long** for a 3 s horizon — the long lines across the view.

The projection now uses the **net displacement over the last 15 history points**,
so one bad frame cannot set the direction, and the speed is capped at 2.5 m/s for
a person and 12 m/s for plant. Longest projection across both clips is now
**7.5 m**, and a wildly oscillating track gets **no path at all** rather than a
bogus one, because its net displacement cancels out.

**Regression:** 349 Python tests and 64 browser tests pass, including 3 physical
size tests, 3 forecast bound tests and 1 post proportion test. Container smoke
exits 0.

## Ghost frames removed, and the helmet glyph — 2026-10-01

### One object, one box

The panel was drawing more boxes than there were objects: the detector returns a
second, weaker box on a person it has already found, offset over the same body.
On the 30sec clip that was visible as several overlapping WORKER boxes on four or
five people.

The existing suppression could not catch these. It merges plant fragments by
class and marks cab operators, but it deliberately never touches a `WORKER`, and
it works in pixels where a duplicate and two people standing close look the same.

The removal is now calibrated on the ground plane instead, and **the thresholds
come from the data rather than from a guess**:

| population | measured ground gap |
|---|---|
| a second box on the same person | **0.17 – 1.08 m** |
| two genuinely separate people whose boxes overlap | **1.59 – 4.57 m** |

`DUPLICATE_PERSON_METRES = 1.2` sits in the gap between those two populations;
`DUPLICATE_MACHINERY_METRES = 3.0` for plant. One object cannot stand in two
places, so a box that overlaps another of its class **and** lands within that
distance of it is dropped, keeping the more confident one.

**Both signals are required, and that is the safety property.** Overlap alone
cannot separate the cases — duplicates measured 0.55–1.00 and separate people
0.62–0.72 on the same clips. Requiring the ground gap as well means a crowd is
never collapsed into one person: two people whose boxes overlap heavily but who
stand 1.6 m apart are both kept, and that is asserted in the tests.

The overlap threshold is 0.25 of the smaller box, not 0.5: two boxes on one
person are often offset — head-and-torso against torso-and-legs — and overlap
only 0.2–0.4. That was measured too, and it is where most of the removals come
from.

Measured across six restored clips: **146 of 2501 served boxes (5.8 %)** were
ghosts and are gone, concentrated in the clip that showed the problem — 144 of
714 boxes, **20 %**, on `30sec_construction`. The other clips lose none or one.
**Zero** pairs at or beyond the threshold are ever dropped; that invariant is
asserted directly.

### The pink glyph beside HARDHAT

A worker emoji was drawn in front of the hardhat note. It rendered as a
flesh-toned figure with an orange helmet, which read as a stray pink frame on the
note chip. The note is now plain text — `HARDHAT: OK` / `NO HARDHAT` / `UNKNOWN` — with the
original wording and colours unchanged.

**Regression:** 357 Python tests and 64 browser tests pass, including 8 ghost
frame tests asserting the safety invariant. Disabling the dedupe was confirmed to
fail those tests. Container smoke exits 0.

## Interface carries no emoji — 2026-10-01

This is a production surface, and pictographic glyphs render differently on every
platform: the worker emoji beside `HARDHAT` came out as a flesh-toned figure with
an orange helmet, which read as a stray pink frame on the note chip. Every glyph
is gone and the interface is plain text.

| where | was | now |
|---|---|---|
| PPE hardhat note | worker emoji + `HARDHAT: OK` | `HARDHAT: OK` / `NO HARDHAT` / `UNKNOWN` |
| PPE vest note | vest emoji + `VEST: OK` | `VEST: OK` / `NO VEST` / `UNKNOWN` |
| scenario dropdown | cassette emoji prefix on restored originals | the title alone, with the `(original)` suffix |
| Rescan button | reload arrow | `Rescan` |
| Media panel button | reload arrow | `Refresh assets` |
| Camera setup page | left arrow | `Back to monitoring` |
| Demo titles | star prefix | the title alone |

Verified against what the two running apps actually serve: `/assets/hub.js`,
`/`, `/setup` and the `/api/v1/hub/sources` payload are all free of emoji on both
18080 and 18083. A browser test now fails if any of the three interface files
gains one.

Nothing else changed: the PPE wording and colours are still the original's, the
`measured` flag still decides whether a hardhat is reported missing, and the
words `NO HARDHAT` / `NO VEST` may only come from a measured record.

**Regression:** 357 Python tests and 48 browser tests pass. Container smoke
exits 0.

## The test suite was overwriting the repository's own configuration — 2026-10-01

**Found while running a 30-agent audit**, which was told it could run the test
suite. Eight tests then failed, asserting that `data/manifests/*.json` match the
original repository. They were right: the files had been rewritten.

`src/graph/context_pipeline.py` and `src/graph/supervisor_agent.py` compile a
shift safety manifest and then persist it to the **relative path
`data/manifests`**, writing both `SHIFT_<id>.json` and `active_manifest.json`:

```python
manifests_dir = "data/manifests"
os.makedirs(manifests_dir, exist_ok=True)
...
with open(active_path, "w", encoding="utf-8") as f:
    json.dump(manifest_dict, f, indent=2)
```

`active_manifest.json` is the checked-in configuration naming the hazard
envelopes in force for the active site. Measured damage: it had been replaced by
`SHIFT_2026-09-07_SITE-01` — **the active site had silently become SITE-01 instead
of SITE-EAST** — and three shift files had their `compiled_at` restamped to the
moment of the run.

Measured, one module at a time, which tests do it:

| module | repository files modified |
|---|---|
| `tests/test_pipelines` | 2 |
| `tests/test_supervisor_agent` | +1 |
| `tests/test_agent_api` | +1 |

So **running the test suite changed which site's safety envelopes were active**,
and because the path was relative it also depended on the working directory the
application was started from.

Three changes:

1. `manifest_selector.manifest_dir()` / `active_manifest_path()` resolve
   `SENTINEL_MANIFEST_DIR` **on every call**, not in a default argument, which is
   evaluated once at import and would ignore a variable set later. The checked-in
   directory stays the default, so production behaviour is unchanged.
2. Both writers use it. The three offending test modules point it at a
   `tempfile.mkdtemp()` for the duration of each test and restore it afterwards.
3. A latent bug this exposed: `manifest_path` became a `Path`, and the line
   `manifest_path.replace("\\", "/")` then called `Path.replace()`, which means
   *rename*, not *substitute*. It is `str(manifest_path).replace(...)` now.

Verified: each of the three modules leaves the repository untouched, and a full
suite run reports **0 modified files under `data/manifests/`**.

**Regression:** 357 Python tests and 65 browser tests pass.

## The 30-agent audit, and what it changed — 2026-10-01

Thirty aspects of the project were audited by thirty agents, each followed by an
adversarial verifier told to falsify rather than agree. **30 aspects, 0 agent
failures.** 6 CRITICAL and 55 HIGH findings were reported; 47 were confirmed by
the verifier and none were refuted. The full triage is in
`deploy/validation/agent-audit/2026-10-01-30-aspect-audit.md`.

### The first attempt failed completely, and the fix is the point

The first run used `pipeline()` with all thirty aspects at once. **All 60 agents
failed.** The prompt had invited them to run the Python test suite, and every run
of that suite loads YOLO and torch — thirty at once starved the machine.

Four changes took it from 0/30 to 30/30:

| change | why |
|---|---|
| waves of five, not thirty | bounded concurrency |
| no test suite, no torch/ultralytics/cv2 imports | the agents are read-only and light; the application was running on the same machine |
| one retry per aspect | a transient failure no longer loses an aspect |
| verify only CRITICAL and HIGH | the quiet aspects cost one agent instead of two |

### Defects fixed as a result

1. **The test suite was overwriting the repository's own configuration.**
   `context_pipeline.py` and `supervisor_agent.py` persisted compiled manifests to
   the relative path `data/manifests`, so any run replaced `active_manifest.json` —
   the hazard envelopes in force for the active site — with whichever shift was
   compiled last. Measured: the active site had silently become SITE-01 instead of
   SITE-EAST, and `test_pipelines`, `test_supervisor_agent` and `test_agent_api`
   each modified repository files. The directory is now resolved per call from
   `SENTINEL_MANIFEST_DIR` (default unchanged) and those three tests point it at a
   temporary directory.
2. **`manifest_path.replace("\\", "/")`** became `Path.replace()` — which means
   *rename*, not *substitute* — once the path became a `Path`. Fixed with `str()`.
3. **`compile_work_permit` raised `NameError`** on the validation-failure path:
   `manifest_dir` was imported inside the `if manifest:` block and used outside it.
   **This was a regression introduced by change 1 the same day**, found by the
   audit, and fixed and verified.
4. **`detect_for_media` returned a 2-tuple** on the disabled path, so
   `SENTINEL_MEDIA_DETECT=0` — documented as "disable the overlay entirely" — made
   every frame a 500. Now returns the documented 3-tuple.
5. **`@lru_cache(maxsize=1)` on `_catalog_signature()`** made the mtime-based
   catalog invalidation dead: the comparison was always true after the first build.
6. **The documented hardened deployment could never persist anything.** The
   read-only recipe mounts `/state`, but the image had no `/state` directory, so
   Docker created the volume root-owned while the application runs as uid 10001.
   Reproduced: `PermissionError: '/state/manifests'`. The image now creates and
   owns `/state/setup`, `/state/surveys` and `/state/manifests` before `USER`, and
   a fresh volume inherits that ownership — verified writable in the running
   container.

### One audit finding I had to correct

The audit called the permit-extraction fall-through CRITICAL without noting the
guard two lines below it. Executed in both modes: **production refuses**
(`RuntimeError`, then the `SENTINEL_MODE` guard raises for an empty list), while
**development fabricates** `['Site Earthmoving']` from an input that says there are
no permits. The defect is real; the blast radius is narrower than claimed. Every
other finding carries the location the auditor cited, but only the ones recorded
above were reproduced by hand.

### Caveats

- The verifier saw only CRITICAL and HIGH findings, so the 112 MEDIUM and LOW
  findings are leads, not findings.
- One CRITICAL was overstated, so the others are not certain either.
- No agent ran the test suite or imported torch, so nothing in the audit rests on
  executed model behaviour.

**Regression:** 357 Python tests and 65 browser tests pass. Repository files are
untouched by a full suite run. Container smoke exits 0.

## The pink "helmet" labels are in the source footage, not in this application — 2026-10-01

Reported as a pink `helmet` indicator overlaying the person detection frame. It is
not drawn by the dashboard, and no code change can remove it.

**Proof.** Frame 94 of `30sec_construction` was decoded straight from
`data/real_videos/30sec_construction.mp4` with OpenCV — no application, no
dashboard, no detector in the path — and the pink `helmet 0.89` / `helmet 0.70`
boxes are present in the decoded pixels at the file's native 1280x720. The same
labels appear in the JPEG the API serves, because the API serves the decoded
frame. The clip is stock footage carrying a `RESONATE PICTURES` watermark and a
third party's baked-in PPE annotations.

Nothing in this repository produces that overlay: the dashboard draws worker boxes
in `#3b82f6` and machinery in `#f59e0b`, both captioned `WORKER` / `HEAVY
EQUIPMENT` / `LIGHT VEHICLE`, and the string `helmet` appears nowhere in
`src/dashboard`. The only occurrences are internal variable names in the
detector's colour heuristic.

**Scope.** Decoded and sampled every restored clip for the label's flat salmon
band in the upper frame:

| clip | frames with baked-in labels |
|---|---|
| `real_videos_30sec_construction` | 2 / 4 |
| `real_videos_Worker_and_excavator_near_barrier` | 3 / 3 |
| `real_videos_Worker_near_heavy_equipment_exca9` | 2 / 3 |
| `test_videos_user_site_video` | 2 / 2 |
| the other seven clips | 0 |

**Four of twelve clips carry a third party's annotations.** There is no clean copy
of any of them in the repository — `data/original_assets_manifest.json` records
each annotated file as the tracked original, and no alternate version of these
files exists on disk.

This matters for interpretation, not just appearance: those labels are **not this
system's detections**, and they must not be read as evidence that the PPE pipeline
ran on that footage.

### The disclosure, as implemented

Chosen: keep the clips and say so in the panel.

- `media.baked_in_annotations(media_id, path, frame_count)` samples six points
  through a clip, looks for the label's flat salmon band in the upper frame, and
  caches the verdict per media. Two hits are required: one frame of that colour is
  a coincidence, and the labels appear only on frames where a head was found.
- `hub.original_frame` serves the verdict as `source_annotations`.
- The camera panel shows, under the `ORIGINAL RECORDING · NOT LIVE` badge:

  > Source recording already contains third-party detection labels. They are not
  > this system's output.

  The element is hidden for the eight clean clips.

Verified per clip against the running app:

| clip | `source_annotations` |
|---|---|
| `real_videos_30sec_construction` | true |
| `real_videos_Worker_and_excavator_near_barrier` | true |
| `real_videos_Worker_near_heavy_equipment_exca9` | true |
| `test_videos_user_site_video` | true |
| `real_videos_10810477-hd_1920_1080_30fps` | false |
| `real_videos_Worker_in_excavator_blind_spot` | false |

**Regression:** 360 Python tests and 66 browser tests pass, including three tests
that assert the four annotated clips are identified and the clean ones are not.
