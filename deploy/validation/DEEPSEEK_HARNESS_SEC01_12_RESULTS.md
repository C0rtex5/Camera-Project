# DeepSeek Harness sprint validation - SEC-01 to SEC-12

**Generated:** 2026-09-29  
**Framework:** DEEPSEEK_HARNESS_SPRINT_VALIDATION.md (evidence-gathering; application code unchanged)

## 1. Tested source and image identity

| Field | Value |
|---|---|
| repository | /var/home/cortex/Projects/SentinelZone-AI |
| git commit | bc8e2c88d376575503e77351f1b123b13c547535 (main) |
| working tree | 79 entries (20 modified, 59 untracked) - uncommitted, so file hashes are the source identifier |
| source snapshot app py | a9ec430069ffc75f5 |
| source snapshot hub py | ec697b6b70411a73f |
| source snapshot media py | 58dcea4784c07c96 |
| source snapshot media detection py | 275108860f2b1952 |
| source snapshot index html | aaed51dd6c770d8f |
| model yolov8n pt | sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 |
| model weights yolo26n pt | sha256 9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef |
| app url source | http://127.0.0.1:18083 |
| app url docker | http://127.0.0.1:18080 |
| docker image | sentinelzone-ai:demo-media |
| docker image id | sha256:9e2e1f2cb7732566320eaded13be54944f783b4f0ff98a0bdce41a0c16e20d3e |
| docker image built | 2026-09-29T17:36:13+01:00 |
| older image not used | sentinelzone-ai:standalone-20260924 (built 2026-09-24) is stopped and was not used as evidence for the current app |
| port 18081 | nothing listening; the previously used separate image is not running |

> The repository has substantial uncommitted work, so the commit alone does not identify
> the tested code. File hashes above are the source snapshot identifier.

## 2. Environment

| Field | Value |
|---|---|
| os | Bazzite 44 (Linux 7.2.7-ogc1.1.fc44.x86_64) |
| cpu | AMD Ryzen 7 5700X, 16 logical cores |
| gpu | NVIDIA GeForce RTX 3060 present, driver 615.71.09; torch 2.14.0+cpu build, CUDA unavailable |
| python | 3.11.16 (.venv-prod) |
| node | v26.8.1 |
| libraries | opencv 4.11.0, torch 2.14.0+cpu, numpy 2.1.3, ultralytics 8.3.134 |
| health 18080 | HTTP 200 HEALTHY, monitoring_ready=false, device=unavailable |
| ready 18080 | HTTP 503 {"ready":false,"cameras":[]} |
| health 18083 | HTTP 200 HEALTHY, monitoring_ready=false, device=unavailable |
| ready 18083 | HTTP 503 {"ready":false,"cameras":[]} |
| live monitoring configured | no - 0 cameras on both instances, no camera draft saved |
| sec01 survey state | 0 records, no selected camera, sec01_status.complete=false on both instances |
| browser automation | none available (no chromium, puppeteer or playwright); no screenshots or browser console capture possible |

## 3. Exact test commands

| Command | Exit | Result |
|---|---|---|
| `.venv-prod/bin/python scripts/run_tests.py` | 0 | 164 tests, OK |
| `node tests/hub.test.cjs` | 0 | 6 passed, 0 failed |
| `node tests/live_dashboard.test.cjs` | 0 | 4 passed, 0 failed |
| `node tests/media_panel.test.cjs` | 0 | 7 passed, 0 failed |
| `python scripts/docker_setup_smoke.py --image sentinelzone-ai:demo-media` | 0 | OFFLINE_HUB_OK, SETUP_INTEGRATION_OK |
| `python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:demo-media` | 0 | RTSP_INTEGRATION_OK (stream, disconnect, reconnect, device, shutdown) |
| `python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:demo-media --setup` | 0 | RTSP_INTEGRATION_OK via the saved setup API |
| `python scripts/docker_ssh_smoke.py --image sentinelzone-ai:demo-media --fixture sentinelzone-ai:ssh-test-fixture` | 0 | SSH_INTEGRATION_OK (key, strict host verification, loopback forward, reconnect, shutdown) |
| `.venv-prod/bin/python scripts/evaluate_detector.py --video-frames 6` | 0 | 398 labelled images measured; report written |

## 4. Verdict tally

| Card | PASS | FAIL | BLOCKED | NOT TESTED | Items | Card result |
|---|---|---|---|---|---|---|
| SEC-01 | 0 | 0 | 8 | 0 | 8 | not complete |
| SEC-02 | 2 | 0 | 3 | 0 | 5 | not complete |
| SEC-03 | 7 | 0 | 0 | 1 | 8 | not complete |
| SEC-04 | 2 | 2 | 2 | 1 | 7 | not complete |
| SEC-05 | 3 | 3 | 3 | 0 | 9 | not complete |
| SEC-06 | 6 | 1 | 1 | 0 | 8 | not complete |
| SEC-07 | 0 | 9 | 0 | 0 | 9 | not complete |
| SEC-08 | 3 | 3 | 1 | 1 | 8 | not complete |
| SEC-09 | 0 | 0 | 11 | 0 | 11 | not complete |
| SEC-10 | 2 | 3 | 3 | 0 | 8 | not complete |
| SEC-11 | 4 | 2 | 1 | 1 | 8 | not complete |
| SEC-12 | 7 | 2 | 2 | 0 | 11 | not complete |
| **All** | **36** | **25** | **35** | **4** | **100** | - |

Items carrying the SEC-09 source duplication: SEC-09.6 (retained and evaluated separately).

## 5. Item results

### SEC-01 - Camera inventory and validation

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-01.1` | List the cameras available to the project. | **BLOCKED** | PHYSICAL SITE CAMERA | Query the survey store and site inventory for the camera list | The application exposes the survey workflow, but the store is empty: 0 records, no selection, sec01_status.complete=false | GET /api/v1/setup/survey on 18080 and 18083; log ui-routes.log<br>Site/IT camera inventory not supplied to this environment |
| `SEC-01.2` | Identify the physical location of each camera. | **BLOCKED** | PHYSICAL SITE CAMERA | Cross-check inventory entries against a physical site walkdown | The setup form accepts a location field and the survey record stores it, but no camera entry exists to verify | src/api/camera_survey.py; deploy/CAMERA_SURVEY.md<br>Site/IT camera inventory not supplied to this environment |
| `SEC-01.3` | Identify the IP address or access endpoint of each camera. | **BLOCKED** | PHYSICAL SITE CAMERA | Attempt TCP reachability to a plant camera endpoint | TCP reachability probing exists (_tcp_check) but no plant endpoint is known to this host | src/api/connection_check.py; RTSP smoke shows the mechanism only<br>No reachability from this host to the plant camera network (camera on a site VLAN) |
| `SEC-01.4` | Verify access to each video stream. | **BLOCKED** | PHYSICAL SITE CAMERA | Open the plant camera RTSP stream and decode a frame | A disposable RTSP fixture decoded and streamed successfully, which validates the mechanism but not any plant stream | deploy/validation/deepseek-harness-2026-09-29/rtsp-smoke-cpu.log (RTSP_STREAMING_OK)<br>No reachability from this host to the plant camera network (camera on a site VLAN) |
| `SEC-01.5` | Identify plant cameras suitable for proximity detection and technically validate video access. | **BLOCKED** | PHYSICAL SITE CAMERA | Validate a plant camera for proximity detection | No plant camera has been supplied; selection is inventory-gated and rejects a camera without location/host/port/path | src/api/camera_survey.py select(); tests/test_camera_survey.py<br>Site/IT camera inventory not supplied to this environment |
| `SEC-01.6` | Validate image resolution and quality. | **BLOCKED** | PHYSICAL SITE CAMERA | Measure resolution and quality on the plant camera | Objective quality screening (min 640x480, sharpness, brightness, contrast) is implemented and exercised on restored recordings only | src/api/connection_check.py; deploy/DETECTION_CALIBRATION.md<br>Site/IT camera inventory not supplied to this environment |
| `SEC-01.7` | Record network or connectivity blockers. | **BLOCKED** | PHYSICAL SITE CAMERA | Record a connectivity block observed while surveying a plant camera | The blocker mechanism is implemented and unit-tested (camera_open_failed, connection_refused, ssh_*), but no plant survey was performed so no real blocker was recorded | src/api/connection_check.py _classify_error; tests/test_connection_check.py<br>Site/IT camera inventory not supplied to this environment |
| `SEC-01.8` | Document the selected camera. | **BLOCKED** | PHYSICAL SITE CAMERA | Read the selected camera from the survey report | The report renders location, host, port, stream path, resolution, quality and disposition, but no camera is selected | GET /api/v1/setup/survey/report.md returns an empty selection<br>Site/IT camera inventory not supplied to this environment |

### SEC-02 - Computer vision environment

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-02.1` | Prepare the technical environment to receive camera video and run computer vision processing. | **PASS** | SYNTHETIC RTSP | Run the disposable RTSP integration runner against the tested image | Real H.264/TCP decode, YOLO detection, telemetry API, disconnect, reconnect and shutdown all succeeded on CPU | rtsp-smoke-cpu.log: RTSP_INTEGRATION_OK, exit 0 |
| `SEC-02.2` | Configure access to the camera stream. | **BLOCKED** | PHYSICAL SITE CAMERA | Point the setup at the plant camera stream | Setup accepts structured host/port/path plus SSH tunnel fields; the RTSP fixture path is proven, the plant path is not reachable | src/api/camera_setup.py; rtsp-smoke-setup-cpu.log: RTSP_INTEGRATION_OK<br>No reachability from this host to the plant camera network (camera on a site VLAN) |
| `SEC-02.3` | Test real-time video processing. | **BLOCKED** | PHYSICAL SITE CAMERA | Stream from the plant camera and measure end-to-end latency | Real-time decode, tracking and detection ran against a disposable RTSP server with disconnect/reconnect, which is not a plant stream | rtsp-smoke-cpu.log: RTSP_DISCONNECT_OK, RTSP_RECONNECT_OK<br>No reachability from this host to the plant camera network (camera on a site VLAN) |
| `SEC-02.4` | Document the dependencies used. | **PASS** | SOURCE/UNIT | Inspect the pinned dependency set and the deployment guide | Pinned runtime lock plus documented operating constraints (single worker, reverse proxy, non-root, read-only root, no credentials in URLs) are present | requirements-runtime.lock; deploy/PRODUCTION.md |
| `SEC-02.5` | Validate initial processing performance. | **BLOCKED** | PHYSICAL SITE CAMERA | Measure processing performance on the intended single-camera host | Measured 62.88 ms median CPU inference for the calibrated detector and a median 6.03 ms frame serve for restored media, but no plant camera stream exists to measure end to end | detector-summary.txt; docker-setup-smoke.log DEMO_API_MEASUREMENT<br>No reachability from this host to the plant camera network (camera on a site VLAN) |

### SEC-03 - Worker detection

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-03.1` | Implement image recognition that identifies workers or people in the camera field of view. | **PASS** | SOURCE/UNIT | Measure person detection on the 398 labelled images with ground truth | Person recall 0.9275 (138 boxes); image-level presence precision 0.8721, recall 0.9845 over 398 labelled images | detector-summary.txt; detector_evaluation.json; scripts/evaluate_detector.py |
| `SEC-03.2` | Select a person-detection model. | **PASS** | SOURCE/UNIT | Record the selected model identity and class mapping | yolov8n.pt (COCO-80) is selected, hash-recorded, and its person class maps to the internal WORKER class | sha256 f59b3d83...; src/perception/detector.py SENTINEL_CLASS_MAPPING |
| `SEC-03.3` | Integrate the model with the camera stream. | **PASS** | SYNTHETIC RTSP | Run the model continuously against a live RTSP stream | The detector ran continuously over a decoded RTSP stream and produced telemetry until disconnect and reconnect | rtsp-smoke-cpu.log: RTSP_STREAMING_OK, RTSP_DEVICE_OK cpu |
| `SEC-03.4` | Show each detected person visually in the image. | **PASS** | DEMO/RECORDING | Draw the served detection boxes on the served frame and inspect the overlay | Camera panel paints the decoded frame and a box per detection; serving a restored frame and drawing its own boxes produced exact alignment (calibration scale error 0.0) | src/dashboard/assets/hub.js draw(); alignment verified on a served frame during the calibration work |
| `SEC-03.5` | Validate multiple people in one frame. | **PASS** | DEMO/RECORDING | Count detections in multi-person frames of the restored recordings | Up to 12 workers detected in a single frame of a restored recording; a 5-person frame was inspected and each detection was correctly placed. Evidence is recorded footage, not plant imagery | detector_evaluation.json videos[] mean_workers_per_frame; alignment frame inspection |
| `SEC-03.6` | Test different lighting conditions. | **NOT TESTED** | SOURCE/UNIT | (not run) compare detections across controlled lighting conditions | No lighting-variation fixture was constructed and no stratified-by-illumination breakdown was produced, so the effect of lighting is unquantified | deploy/DETECTION_CALIBRATION.md records brightness/contrast thresholds but no lighting study<br>Check was available but was not run: no lighting-conditioned subset was prepared |
| `SEC-03.7` | Record test results. | **PASS** | SOURCE/UNIT | Publish the measured detection results and per-class recall | Full report written with box and image-level metrics, per-ground-truth-class recall, coverage and calibration error | data/reports/detector_evaluation.json; deploy/DETECTION_CALIBRATION.md |
| `SEC-03.8` | Document model limitations. | **PASS** | SOURCE/UNIT | Check the documented limitations against measured behaviour | Excavator recall 0.20 versus person 0.93 is documented with its cause (COCO-80 has no excavator class) and the training step needed to close it | deploy/DETECTION_CALIBRATION.md section 4 |

### SEC-04 - Virtual hazard zone

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-04.1` | Select the machine and zone for the first PoC. | **BLOCKED** | PHYSICAL SITE CAMERA | Select a plant machine and hazard zone for the PoC | Demo scenarios carry illustrative envelopes, but no plant machine or zone has been identified for the PoC | src/api/demo_service.py KNOWN_SCENARIO_CONFIGS (marked illustrative)<br>Site/IT camera inventory not supplied to this environment |
| `SEC-04.2` | Identify the hazard area visually. | **BLOCKED** | PHYSICAL SITE CAMERA | Mark the hazard area on plant imagery | No plant imagery or camera view of a hazard area is available to mark | No plant image in the environment<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-04.3` | Create a virtual zone over the camera image. | **FAIL** | SOURCE/UNIT | Inspect what the camera panel paints and where hazard polygons are rendered | The camera panel paints only the decoded frame and detection boxes. Hazard polygons are rendered only in the separate 3D twin scene; no zone overlay exists on the camera image, which the brief states is not sufficient | src/dashboard/assets/hub.js: draw() has no polygon path; polygon rendering only at the createTwin hazardGroup<br>Required behaviour absent: the camera image has no zone overlay |
| `SEC-04.4` | Allow the zone boundaries to be configured. | **PASS** | SOURCE/UNIT | Load a manifest with a configured exclusion polygon | Envelopes are loaded from the active manifest (polygon_metric_epsg3857) with ttc_multiplier and exempt_entities, and demo scenarios ship envelopes | src/edge/conflict_engine.py load_manifest_envelopes; data/manifests/active_manifest.json |
| `SEC-04.5` | Identify a person's entry into and exit from the zone. | **FAIL** | SOURCE/UNIT | Search for zone entry/exit state transitions in the risk pipeline | The zone polygon is used only as a containment test inside evaluate_pair_risk (poly.covers). No entry, continued-presence or exit transition is tracked, and the only state machine is risk escalation | src/edge/conflict_engine.py; no entry/exit transition exists<br>Required behaviour absent: no entry/exit transition tracking exists |
| `SEC-04.6` | Test different zone positions. | **NOT TESTED** | SOURCE/UNIT | (not run) reconfigure the envelope polygon at several positions and compare | Zone geometry is configurable, but no position-variation comparison was executed, so the behaviour under different placements is unverified | load_manifest_envelopes exists; no variation test in tests/<br>Check was available but was not run: no zone-position variation test was written |
| `SEC-04.7` | Document the rule used. | **PASS** | SOURCE/UNIT | Read the implemented zone rule and its specification | The rule is implemented in conflict_engine (containment raises the separation radius and TTC multiplier, exempt entities are ignored) and specified in agent.md. The worker-to-machinery proximity ladder and the PPE colour rule are now also restated in the deployment guides, with the DANGER radius tied to the engine's own r_col | src/edge/conflict_engine.py evaluate_pair_risk; agent.md; deploy/SAFETY_EVENT_CONTRACT.md |

### SEC-05 - Worker and machine proximity

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-05.1` | Define the proximity criterion. | **PASS** | SOURCE/UNIT | Read the proximity criterion in the risk engine | Collision radius r_col = max(min_separation_distance, footprint_radius_a + footprint_radius_b), raised inside a zone by the envelope separation, with time-to-collision and mode-mass probability thresholds. A worker-to-machinery ladder (DANGER at or inside r_col = 3.3 m, NEAR to 8.0 m, SAFE beyond, NO_MACHINE when no machinery is in frame) is also defined for restored recordings; the 3.3 m band is the engine's own radius, the 8.0 m band is an unvalidated engineering default | src/edge/conflict_engine.py evaluate_pair_risk; src/api/media_tracks.py; tests/test_conflict_engine.py; tests/test_ppe_proximity_and_separation.py |
| `SEC-05.2` | Use the detected person's position. | **PASS** | SOURCE/UNIT | Trace detections into the footpoint used for risk | Detector boxes are converted to a ground footpoint and carried as the tracked position mu_x/mu/y used for containment and separation. Restored recordings now do the same: a per-recording fitted ground plane maps the box footpoint to metric coordinates, and that metric position drives both zone occupancy and the proximity grade The plane is fitted from an observed person rather than an assumed near band: the previous assumption solved to a 0.84 m camera height, below standing eye level, and made people 0.17 m wide. Measured camera heights are now 2.6-5.4 m and person widths 0.46-0.83 m. The fit is a property of the recording, verified deterministic from frames 5, 60 and 120, and carries an `anchored` flag because absolute measurements on the fallback plane are not meaningful. Person fragments are kept off the ground plane by MIN_PERSON_BOX_HEIGHT_PX and MIN_PLAUSIBLE_PERSON_WIDTH_M while their boxes stay on the camera frame. | src/edge/edge_runtime.py; src/edge/conflict_engine.py; src/api/media_tracks.py; src/api/media_tracks.py fit_ground_plane, GroundPlane.anchored; tests/test_calibration_and_ghosts.py |
| `SEC-05.3` | Associate the person with the hazard zone. | **PASS** | SOURCE/UNIT | Test the containment association between person and zone | The person position and the zone are tested against the same CANONICAL_ZONE constant, and the polygon drawn in the 3D canvas is positioned and scaled from that same packet field, so the drawn rectangle and the tested rectangle cannot diverge. The association is real; the zone's depth band is fitted to each recording rather than surveyed, so it is a working-area test and not a validated hazard boundary The depth band was re-fitted after the plane recalibration: it is now y[4,11] m, which contains 144 of 161 observed workers on the calibrated plane against 124 on the previous band. It remains a fitted working area, not a surveyed hazard boundary, and the 0.5 m shoulder anchor it inherits is documented as an assumption rather than a site survey. | src/api/media_tracks.py in_canonical_zone; src/dashboard/assets/hub.js trenchMesh.position/scale from packet.zone; tests/test_3d_pose_and_zone_calibration.py; deploy/SAFETY_EVENT_CONTRACT.md ground-plane calibration section |
| `SEC-05.4` | Detect entry into the zone. | **FAIL** | SOURCE/UNIT | Search for an entry transition into the zone | No entry event is produced. Zone membership is re-evaluated per frame with no prior state, so an entry cannot be distinguished from steady occupancy | src/edge/conflict_engine.py has no entry transition<br>Required behaviour absent: zone membership is stateless per frame |
| `SEC-05.5` | Detect continued presence in the zone. | **FAIL** | SOURCE/UNIT | Search for a continued-presence state | No presence state is carried between frames; only risk-level escalation and de-escalation are debounced | src/edge/conflict_engine.py Debouncer only<br>Required behaviour absent: no presence state is carried between frames |
| `SEC-05.6` | Detect exit from the zone. | **FAIL** | SOURCE/UNIT | Search for an exit transition from the zone | No exit event is produced for the same reason as entry: occupancy is stateless per frame | src/edge/conflict_engine.py has no exit transition<br>Required behaviour absent: no exit transition exists |
| `SEC-05.7` | Test a safe scenario. | **BLOCKED** | PHYSICAL SITE CAMERA | Play back a plant scenario where the worker stays clear of the machine | A diverging-path safe case is covered by unit tests and restored recordings exist, but the plant scenario required by the card was not available | tests/test_conflict_engine.py test_diverging_safe_paths<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-05.8` | Test a risk scenario. | **BLOCKED** | PHYSICAL SITE CAMERA | Play back a plant scenario where the worker approaches the machine | An imminent-collision risk case is covered by unit tests, but the plant scenario required by the card was not available | tests/test_conflict_engine.py test_imminent_head_on_collision<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-05.9` | Record the results. | **BLOCKED** | PHYSICAL SITE CAMERA | Record the safe and risk scenario results | Contingent on 5.7 and 5.8, which are blocked; no plant scenario result can be recorded | depends on SEC-05.7 and SEC-05.8<br>No consented plant recording with reviewed frame-level annotations supplied |

### SEC-06 - Risk classification

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-06.1` | Define the Safe state. | **PASS** | SOURCE/UNIT | Read the normal level in the risk engine | NORMAL_LEVEL_0 is returned when the pair is clear or when the person is an exempt entity inside a zone | src/edge/conflict_engine.py |
| `SEC-06.2` | Define the Attention state. | **PASS** | SOURCE/UNIT | Read the advisory level in the risk engine | ADVISORY_LEVEL_1 is a defined band (fixed 5.0 s reference and 0.20 probability) | src/edge/conflict_engine.py level thresholds |
| `SEC-06.3` | Define the Risk state. | **PASS** | SOURCE/UNIT | Read the warning and critical levels | WARNING_LEVEL_2 and CRITICAL_LEVEL_3 are defined bands. The engine exposes four internal levels where the card names three business states; see SEC-06.8 for the missing documented mapping | src/edge/conflict_engine.py level_map |
| `SEC-06.4` | Define the condition for each state. | **PASS** | SOURCE/UNIT | Read the threshold conditions for each level | Each level has a numeric condition: time-to-collision threshold scaled by the envelope multiplier plus a mode-mass probability threshold (critical uses p_threshold, warning 0.6x, advisory 5.0 s / 0.20) | src/edge/conflict_engine.py thresholds |
| `SEC-06.5` | Associate proximity with the risk level. | **PASS** | SOURCE/UNIT | Trace proximity and forecast into the level decision | Separation radius and time-to-collision of the forecast modes produce p_col, which selects the level; zone multiplier and exemptions modify the inputs | src/edge/conflict_engine.py evaluate_pair_risk |
| `SEC-06.6` | Test changes between states. | **PASS** | SOURCE/UNIT | Run the debouncer transition tests | Escalation and de-escalation are asserted with the frame debounce, plus imminent-collision, diverging, mixture-mode and late-peak cases | tests/test_conflict_engine.py test_debouncer_escalation_and_deescalation (6 tests, all ok) |
| `SEC-06.7` | Validate behavior on real video. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay plant footage and compare level with reality | Levels were exercised on synthetic and recorded material only; no plant footage was available to compare level decisions with reality | rtsp-smoke-cpu.log (synthetic); restored recordings<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-06.8` | Document the rules. | **FAIL** | SOURCE/UNIT | Search the repository for a mapping of the four internal levels to Safe/Attention/Risk | No document maps NORMAL/ADVISORY/WARNING/CRITICAL to the Safe/Attention/Risk states the card defines, and four internal levels do not map one-to-one onto three business states | grep over deploy/ and README.md found no mapping; only agent.md mathematics<br>Required documentation absent: no mapping of the four internal levels to Safe/Attention/Risk |

### SEC-07 - Structured safety event

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-07.1` | Create the event structure. | **FAIL** | SOURCE/UNIT | Look for a schema describing the safety event | No event schema exists. The only incident model is an adjudication verdict (source-camera free text). /api/v1/incidents/publish accepts an untyped dict and relays it | src/api/app.py publish_edge_incident(incident_packet: dict); src/schemas/contracts.py<br>Required behaviour absent: no event schema exists; the relay takes an untyped dict |
| `SEC-07.2` | Record the source camera. | **FAIL** | SOURCE/UNIT | Check for a camera field in any emitted event | The relay forwards whatever the caller sends; nothing populates a camera identifier from the pipeline | src/api/app.py publish_edge_incident<br>Required behaviour absent: nothing populates a camera identifier on an event |
| `SEC-07.3` | Record the zone or machine. | **FAIL** | SOURCE/UNIT | Check for a zone or machine field in any emitted event | No zone or machine identifier is attached to any emitted event | no event producer in src/<br>Required behaviour absent: no zone or machine identifier is attached to any event |
| `SEC-07.4` | Record the date and time. | **FAIL** | SOURCE/UNIT | Check for a timestamp in any emitted event | Telemetry carries captured_at inside a frame packet, but no event is produced, so no event timestamp exists | no event producer in src/<br>Required behaviour absent: no event carries a timestamp |
| `SEC-07.5` | Record the risk level. | **FAIL** | SOURCE/UNIT | Check for a risk level field in any emitted event | The per-frame packet carries a debounced alarm and per-pair risk, but no event object records the level at the moment of change | no event producer in src/<br>Required behaviour absent: no event records the risk level at transition |
| `SEC-07.6` | Record the event type. | **FAIL** | SOURCE/UNIT | Check for an event type field in any emitted event | No event type field exists; the relay passes an opaque dict through | src/api/app.py publish_edge_incident<br>Required behaviour absent: no event type field exists |
| `SEC-07.7` | Test automatic event generation. | **FAIL** | SYNTHETIC RTSP | Drive the live pipeline and watch for an event on a risk transition | Nothing generates an event from a risk transition. Zone occupancy transitions now do emit ZONE_ENTRY, ZONE_OCCUPANCY and ZONE_EXIT automatically for restored recordings, but the risk-level transition still emits nothing: the only writer of incident-like records appends to an active-learning queue from an explicit adjudication call | src/graph/adjudication_pipeline.py; /api/v1/incidents/publish is a caller-driven relay; src/api/media_tracks.py emits zone events only<br>Contrary result: driving the live pipeline produced no event on a risk transition |
| `SEC-07.8` | Validate the event format. | **FAIL** | SOURCE/UNIT | Attempt to validate the event format | There is no event format to validate; the relay accepts any JSON object without schema checks | POST /api/v1/incidents/publish with an arbitrary dict returned BROADCAST_SUCCESS<br>Required behaviour absent: there is no event format to validate |
| `SEC-07.9` | Document the event contract. | **FAIL** | SOURCE/UNIT | Search the documentation for an event contract | No event contract is documented in README.md or deploy/ | grep 'event contract|event schema|event structure' over deploy/ and README.md returned nothing<br>Required documentation absent: no event contract is documented |

### SEC-08 - Visual risk alert

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-08.1` | Show the detected person. | **PASS** | DEMO/RECORDING | Inspect the camera panel rendering for a detected person | Each detection is drawn as a labelled box over the decoded frame, and served frames carry per-frame boxes | src/dashboard/assets/hub.js draw(); live frame packets carry detections |
| `SEC-08.2` | Show the hazard zone. | **FAIL** | SOURCE/UNIT | Inspect the camera panel for a hazard zone overlay | The camera panel shows no zone. Hazard geometry is drawn only in the 3D twin, and a restored recording carries no hazard envelope at all | hub.js draw() has no polygon path; restored packets have hazards=[]<br>Required behaviour absent: the camera panel shows no hazard zone |
| `SEC-08.3` | Show the current state. | **PASS** | SOURCE/UNIT | Inspect the state indicators on the camera panel | State is shown by the alert badge, the telemetry tag, the TTC/collision metrics and the forecast status; with no camera these read UNAVAILABLE | src/dashboard/index.html system-alert-badge, cam-telemetry-tag, metric-ttc, gnn-status |
| `SEC-08.4` | Highlight a risk situation. | **PASS** | SOURCE/UNIT | Inspect how a risk situation is highlighted | Risk is highlighted by the debounced alert badge, the time-to-collision and collision-mode metrics, and the red hazard geometry in the twin | src/dashboard/assets/hub.js display(); tests/test_conflict_engine.py escalation cases |
| `SEC-08.5` | Show the machine identifier. | **FAIL** | SOURCE/UNIT | Search the camera panel for a machine identifier | No machine identifier exists anywhere in the camera panel; the DOM has no element for it and the detector emits class ids, not machine identities | grep 'machine' src/dashboard/index.html returns no matches<br>Required behaviour absent: no machine identifier exists in the camera panel |
| `SEC-08.6` | Show the timestamp. | **FAIL** | SOURCE/UNIT | Search the camera panel for a timestamp | No timestamp element exists in the camera panel; captured_at exists only inside the frame packet and is never displayed | grep 'timestamp|captured_at' src/dashboard/index.html returns no matches<br>Required behaviour absent: no timestamp element exists in the camera panel |
| `SEC-08.7` | Test a real-time alert. | **BLOCKED** | PHYSICAL SITE CAMERA | Trigger an alert on the plant camera stream | Alerts were exercised on a disposable RTSP stream and recorded material; no plant camera stream exists to alert on | rtsp-smoke-cpu.log<br>No reachability from this host to the plant camera network (camera on a site VLAN) |
| `SEC-08.8` | Validate alert legibility. | **NOT TESTED** | PHYSICAL SITE CAMERA | (not run) assess alert legibility on a real display | No headless browser or display is available in this environment, so legibility could not be assessed on rendered output; only static DOM inspection was possible | no chromium, puppeteer or playwright available<br>Check was available but was not run: no browser rendering available |

### SEC-09 - Validation with real plant imagery

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-09.1` | Test a worker outside the zone. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.2` | Test a worker entering the zone. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.3` | Test a worker remaining in the zone. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.4` | Test a worker leaving the zone. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.5` | Test more than one person. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.6` | Test more than one person. (Duplicated in the source card; retained.) | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.7` | Test different lighting conditions. | **BLOCKED** | PHYSICAL SITE CAMERA | Replay a consented plant recording and score detections against reviewed annotations | No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review | no plant recording in the environment; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.8` | Record false positives. | **BLOCKED** | PHYSICAL SITE CAMERA | Tally false positives against reviewed plant annotations | False positives can be measured (28 person-presence false-alarm images) but only on the public Roboflow dataset, which is not the plant footage this card requires | detector-summary.txt image-level presence; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.9` | Record false negatives. | **BLOCKED** | PHYSICAL SITE CAMERA | Tally false negatives against reviewed plant annotations | False negatives can be measured (3 person-presence misses) but only on the public dataset, not the plant footage this card requires | detector-summary.txt image-level presence; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.10` | Record performance problems. | **BLOCKED** | PHYSICAL SITE CAMERA | Record performance problems observed on plant footage | Detector limitations are recorded from public data (excavator recall 0.20, 51 missed heavy-machinery images); plant-specific performance problems are unknown | deploy/DETECTION_CALIBRATION.md; No consented plant recording with reviewed frame-level annotations supplied<br>No consented plant recording with reviewed frame-level annotations supplied |
| `SEC-09.11` | Consolidate the results. | **BLOCKED** | PHYSICAL SITE CAMERA | Consolidate the plant validation results | Consolidation is contingent on 9.1-9.10, all of which are blocked without plant recordings and reviewed annotations | depends on SEC-09.1 to SEC-09.10<br>No consented plant recording with reviewed frame-level annotations supplied |

### SEC-10 - Digital twin integration preparation

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-10.1` | Identify the corresponding machine in the digital twin. | **BLOCKED** | PHYSICAL SITE CAMERA | Identify the plant machine in the digital twin | The twin renders a generic ground plane with track markers; no plant machine model or machine identity is available | src/dashboard/assets/hub.js createTwin GridHelper; No digital-twin interface or vendor SDK made available<br>No digital-twin interface or vendor SDK made available |
| `SEC-10.2` | Map camera to machine and zone. | **BLOCKED** | PHYSICAL SITE CAMERA | Map a plant camera to a machine and zone | No camera-to-machine mapping exists; the survey model stops at camera inventory and a selected camera | src/api/camera_survey.py; No digital-twin interface or vendor SDK made available<br>No digital-twin interface or vendor SDK made available |
| `SEC-10.3` | Define the communication structure. | **FAIL** | SOURCE/UNIT | Inspect the transports offered to an external consumer | There is a caller-driven WebSocket relay and an optional in-app MQTT telemetry client, but no defined external-twin message structure, handshake or topic contract | src/api/app.py publish_edge_incident; src/edge/edge_runtime.py mqtt (optional, off by default)<br>Required behaviour absent: no external-twin message structure, handshake or topic contract |
| `SEC-10.4` | Define how the digital twin receives the event. | **FAIL** | SOURCE/UNIT | Look for a specification of how a twin would receive an event | No such specification exists, and there is no event to deliver (see SEC-07.1) | no event contract documented; No digital-twin interface or vendor SDK made available<br>Required behaviour absent: no specification of twin event delivery, and no event exists |
| `SEC-10.5` | Define the visual representation of risk. | **PASS** | SOURCE/UNIT | Inspect the twin overlay legend and colours | Risk is represented by a legend and fixed colours: red configured hazard, blue worker tracks, amber equipment, plus a red semi-transparent zone shape | src/dashboard/index.html twin-overlay-info; hub.js hazard mesh colour 0xef4444 |
| `SEC-10.6` | Define machine and zone states. | **FAIL** | SOURCE/UNIT | Look for machine and zone state definitions | Zone state is implied by the red overlay, but no machine state is defined anywhere and the twin has no machine representation to carry one The twin now carries a machine proxy and per-agent motion state, but that is a drawing, not a machine state: the conflict engine still has no machine entity, and the proxy has no identity. The verdict is unchanged for that reason, not because nothing was added. | no machine state in src/; No digital-twin interface or vendor SDK made available; src/dashboard/assets/hub.js createTwin getOrCreateAgentMesh<br>Required behaviour absent: no machine state is defined and the twin has no machine |
| `SEC-10.7` | Create a visual mockup or prototype. | **PASS** | SOURCE/UNIT | Exercise the working Three.js prototype in the running app | A working Three.js prototype renders on the metric ground plane with worker tracks, forecast paths and the hazard polygon, driven by the live hub | src/dashboard/assets/hub.js createTwin; /api/v1/hub/demo/<scenario>/frames/<n> returns 200 |
| `SEC-10.8` | Validate the feasibility of future integration. | **BLOCKED** | PHYSICAL SITE CAMERA | Validate feasibility with the twin vendor | Feasibility cannot be assessed without the digital-twin interface, SDK or vendor schema | No digital-twin interface or vendor SDK made available<br>No digital-twin interface or vendor SDK made available |

### SEC-11 - Three-dimensional safety prototype

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-11.1` | Select the PoC machine. | **BLOCKED** | PHYSICAL SITE CAMERA | Select a plant machine for the 3D prototype | Demo scenarios name an excavator scenario but no plant machine is identified, and the twin holds no machine model | src/api/demo_service.py; No digital-twin interface or vendor SDK made available<br>No digital-twin interface or vendor SDK made available |
| `SEC-11.2` | Use or create a simplified 3D model. | **PASS** | SOURCE/UNIT | Inspect the 3D scene contents | A simplified machine model now exists. Each HEAVY_EQUIPMENT track is drawn as a hull and a cab, both unit boxes scaled to the footprint measured from its own detection, keeping the original repository's 2.6:5.0:1.8 proportions, with the hull pushed forward by half its measured length so it never covers the ground its footpoint stands on. The zone is drawn as the original 20x7 trench plane, positioned and scaled per frame from packet.zone so the drawn rectangle is the tested rectangle. Scope, stated plainly: this is a generic per-class proxy fitted to a detection box, not a surveyed or vendor-supplied model, and it carries no machine identity - the camera-to-machine mapping remains BLOCKED at SEC-10.2 and SEC-11.1. | src/dashboard/assets/hub.js createTwin getOrCreateAgentMesh, update() hull/cab scaling from trk.footprint; src/api/media_tracks.py MediaTracker._advance<br>Required behaviour absent: the 3D scene contains no machine geometry |
| `SEC-11.3` | Represent the safety zone. | **PASS** | SOURCE/UNIT | Confirm the zone geometry is rendered in 3D | The configured zone polygon is rendered as a semi-transparent shape on the metric ground plane | hub.js hazardGroup ShapeGeometry; twin-overlay-info legend |
| `SEC-11.4` | Represent the worker. | **PASS** | SOURCE/UNIT | Confirm a worker is represented in 3D | A worker is drawn as a straight-edged footprint polygon sized from its own measured frame and turned with the group to the walking direction, with the original cylinder post as the direction indicator. Each track keeps its own path line: two shared lines previously meant only the last-drawn agent had a visible trail. The rendered angle eases toward the measured heading and is capped per frame, because a single bad frame swings the raw heading by up to 175 degrees. The measured heading itself is unchanged on the packet. The view frames the agents rather than holding a fixed 42 m over a 50 m grid, and stops overriding a user's own wheel-zoom. | src/dashboard/assets/hub.js createTwin update(), trailLines per track, view auto-framing; tests/safety_zone_ui.test.cjs |
| `SEC-11.5` | Represent the Safe state. | **FAIL** | SOURCE/UNIT | Look for a safe-state representation in the 3D scene | The 3D scene has no state representation: the hazard polygon is always drawn identically and no safe-state geometry or colour change exists. State is only conveyed in the 2D panel The trench is now positioned and scaled per frame from the packet zone rather than drawn identically, but no safe-state geometry or colour change exists; state is still conveyed only in the 2D panel. | hub.js createTwin renders no state change<br>Required behaviour absent: the 3D scene has no safe-state representation |
| `SEC-11.6` | Represent the Risk state. | **FAIL** | SOURCE/UNIT | Look for a risk-state representation in the 3D scene | The 3D scene does not change with risk level; forecast paths are drawn in the same style regardless of risk and the alarm lives in the 2D panel The red ring in the scene marks a zone exit, not a risk level, so the risk-driven styling this card asks for is still absent and the alarm still lives in the 2D panel. | hub.js createTwin update() has no risk-driven styling<br>Required behaviour absent: the 3D scene does not change with risk level |
| `SEC-11.7` | Simulate a real-time state change. | **NOT TESTED** | SOURCE/UNIT | (not run) drive a state change in the browser and confirm the 3D scene reacts | The twin update path is exercised by unit tests and by served frame packets, but no browser rendering was available to confirm a visible state change in the live 3D scene A browser test now builds the real module in a sandbox and drives its update path, which is stronger than the packet-only exercise, but it still renders against a stubbed Three.js rather than a GPU, so this stays NOT TESTED. | no headless browser available<br>Check was available but was not run: no browser rendering available |
| `SEC-11.8` | Prepare a demonstration. | **PASS** | SOURCE/UNIT | Exercise the demonstrable path end to end | A demonstrable path exists: the hub serves restored recordings with calibrated detections, the twin renders tracks and zones, and the setup page drives camera configuration | GET / and /setup return 200; /api/v1/hub/demo/... returns frames with detections |

### SEC-12 - PoC documentation and closeout

| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |
|---|---|---|---|---|---|---|
| `SEC-12.1` | Document the architecture. | **PASS** | SOURCE/UNIT | Review the architecture documentation | Architecture is documented at system and component level, including the edge physics layer, the kinematic/forecast chain and the agentic layer | README.md; agent.md |
| `SEC-12.2` | Document the camera used. | **BLOCKED** | PHYSICAL SITE CAMERA | Name the camera used in the PoC | No plant camera has been selected, so there is no camera to document | GET /api/v1/setup/survey/report.md shows no selected camera<br>Site/IT camera inventory not supplied to this environment |
| `SEC-12.3` | Document the computer vision model. | **PASS** | SOURCE/UNIT | Check the recorded model identity and its measured behaviour | Model file, hash, class mapping, measured precision/recall and the excavator limitation are recorded | deploy/DETECTION_CALIBRATION.md; sha256 f59b3d83... |
| `SEC-12.4` | Document the proximity rules. | **PASS** | SOURCE/UNIT | Locate the proximity rule documentation | The proximity rule is specified in the repository's mathematical documentation, implemented in the risk engine, and now also restated in the deployment guides for operators, including the worker-to-machinery ladder and the fact that its NEAR band is an unvalidated default. The trench depth band is documented as fitted per recording rather than surveyed, and its unvalidated status is stated alongside it The ladder was re-measured on the corrected scale: DANGER 7.5%, NEAR 21.7%, SAFE 20.5%, NO_MACHINE 50.3% of 161 worker states, so it discriminates where the miscalibrated scale compressed it. The DANGER radius is still the engine's own r_col and the NEAR band is still an unvalidated default. | agent.md; src/edge/conflict_engine.py evaluate_pair_risk; deploy/SAFETY_EVENT_CONTRACT.md; deploy/VALIDATION.md |
| `SEC-12.5` | Document the risk levels. | **FAIL** | SOURCE/UNIT | Search the documentation for the risk level mapping | The four internal levels are not documented in terms of the card's Safe/Attention/Risk states, and no mapping between them exists in any document | grep for a level-to-state mapping over deploy/ and README.md returned nothing<br>Required documentation absent: internal levels are not documented against the card's business states |
| `SEC-12.6` | Document the event format. | **FAIL** | SOURCE/UNIT | Search the documentation for the event format | There is no event format to document, because no event structure exists (see SEC-07.1) | no event schema or contract in src/ or deploy/<br>Required documentation absent: there is no event format to document |
| `SEC-12.7` | Record test results. | **PASS** | SOURCE/UNIT | Review the recorded test results and this validation | 164 Python tests, 17 browser tests and the three isolated integration runners are recorded with exact commands, exit codes and logs, together with the detector measurement | deploy/validation/deepseek-harness-2026-09-29/*.log; deploy/VALIDATION.md |
| `SEC-12.8` | Record limitations. | **PASS** | SOURCE/UNIT | Review the recorded limitations | Recorded limitations include the COCO-only detector, the excavator recall gap, the partially labelled dataset, the absence of site evidence and the advisory-only status of the PoC | deploy/DETECTION_CALIBRATION.md; this report section 9 |
| `SEC-12.9` | Record problems encountered. | **PASS** | SOURCE/UNIT | Review the recorded problems and gaps | Problems are recorded with evidence: missing camera overlay, missing zone/machine/timestamp in alerts, absent event structure, absent entry/exit handling, the 384-entry cache eviction stall and the shared-model prewarm serialisation; the trenchMesh block-scoping defect that blanked the camera panel, the track-id array misaligning when an operator was skipped, and the fragment-width filter that emptied two clips until it was gated on an anchored plane | this report section 7; deploy/VALIDATION.md |
| `SEC-12.10` | Define next steps. | **PASS** | SOURCE/UNIT | Review the defined next steps | Next steps are defined in dependency order in this report, with the site inputs required to unblock each card | this report section 8 |
| `SEC-12.11` | Deliver the documentation to the team. | **BLOCKED** | PHYSICAL SITE CAMERA | Deliver the documentation to the team and confirm receipt | No delivery has occurred. This report and the deploy guides exist in the repository only; nothing has been sent to the project team and no delivery channel or acknowledgement is available from this environment | no delivery channel available; No digital-twin interface or vendor SDK made available<br>Delivery channel and team acknowledgement not available in this environment; no delivery has occurred |

## 6. Card summary

- **SEC-01 Camera inventory and validation**: 0/8 pass; 0 fail, 8 blocked, 0 not tested.
- **SEC-02 Computer vision environment**: 2/5 pass; 0 fail, 3 blocked, 0 not tested.
- **SEC-03 Worker detection**: 7/8 pass; 0 fail, 0 blocked, 1 not tested.
- **SEC-04 Virtual hazard zone**: 2/7 pass; 2 fail, 2 blocked, 1 not tested.
- **SEC-05 Worker and machine proximity**: 3/9 pass; 3 fail, 3 blocked, 0 not tested.
- **SEC-06 Risk classification**: 6/8 pass; 1 fail, 1 blocked, 0 not tested.
- **SEC-07 Structured safety event**: 0/9 pass; 9 fail, 0 blocked, 0 not tested.
- **SEC-08 Visual risk alert**: 3/8 pass; 3 fail, 1 blocked, 1 not tested.
- **SEC-09 Validation with real plant imagery**: 0/11 pass; 0 fail, 11 blocked, 0 not tested.
- **SEC-10 Digital twin integration preparation**: 2/8 pass; 3 fail, 3 blocked, 0 not tested.
- **SEC-11 Three-dimensional safety prototype**: 4/8 pass; 2 fail, 1 blocked, 1 not tested.
- **SEC-12 PoC documentation and closeout**: 7/11 pass; 2 fail, 2 blocked, 0 not tested.

## 7. Prioritized gap list

- `SEC-04.3` **FAIL** - Required behaviour absent: the camera image has no zone overlay
- `SEC-04.5` **FAIL** - Required behaviour absent: no entry/exit transition tracking exists
- `SEC-05.4` **FAIL** - Required behaviour absent: zone membership is stateless per frame
- `SEC-05.5` **FAIL** - Required behaviour absent: no presence state is carried between frames
- `SEC-05.6` **FAIL** - Required behaviour absent: no exit transition exists
- `SEC-06.8` **FAIL** - Required documentation absent: no mapping of the four internal levels to Safe/Attention/Risk
- `SEC-07.1` **FAIL** - Required behaviour absent: no event schema exists; the relay takes an untyped dict
- `SEC-07.2` **FAIL** - Required behaviour absent: nothing populates a camera identifier on an event
- `SEC-07.3` **FAIL** - Required behaviour absent: no zone or machine identifier is attached to any event
- `SEC-07.4` **FAIL** - Required behaviour absent: no event carries a timestamp
- `SEC-07.5` **FAIL** - Required behaviour absent: no event records the risk level at transition
- `SEC-07.6` **FAIL** - Required behaviour absent: no event type field exists
- `SEC-07.7` **FAIL** - Contrary result: driving the live pipeline produced no event on a risk transition
- `SEC-07.8` **FAIL** - Required behaviour absent: there is no event format to validate
- `SEC-07.9` **FAIL** - Required documentation absent: no event contract is documented
- `SEC-08.2` **FAIL** - Required behaviour absent: the camera panel shows no hazard zone
- `SEC-08.5` **FAIL** - Required behaviour absent: no machine identifier exists in the camera panel
- `SEC-08.6` **FAIL** - Required behaviour absent: no timestamp element exists in the camera panel
- `SEC-10.3` **FAIL** - Required behaviour absent: no external-twin message structure, handshake or topic contract
- `SEC-10.4` **FAIL** - Required behaviour absent: no specification of twin event delivery, and no event exists
- `SEC-10.6` **FAIL** - Required behaviour absent: no machine state is defined and the twin has no machine
- `SEC-11.5` **FAIL** - Required behaviour absent: the 3D scene has no safe-state representation
- `SEC-11.6` **FAIL** - Required behaviour absent: the 3D scene does not change with risk level
- `SEC-12.5` **FAIL** - Required documentation absent: internal levels are not documented against the card's business states
- `SEC-12.6` **FAIL** - Required documentation absent: there is no event format to document
- `SEC-03.6` **NOT TESTED** - Check was available but was not run: no lighting-conditioned subset was prepared
- `SEC-04.6` **NOT TESTED** - Check was available but was not run: no zone-position variation test was written
- `SEC-08.8` **NOT TESTED** - Check was available but was not run: no browser rendering available
- `SEC-11.7` **NOT TESTED** - Check was available but was not run: no browser rendering available
- `SEC-01.1` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-01.2` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-01.3` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-01.4` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-01.5` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-01.6` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-01.7` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-01.8` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-02.2` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-02.3` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-02.5` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-04.1` **BLOCKED** - Site/IT camera inventory not supplied to this environment
- `SEC-04.2` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-05.7` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-05.8` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-05.9` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-06.7` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-08.7` **BLOCKED** - No reachability from this host to the plant camera network (camera on a site VLAN)
- `SEC-09.1` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.10` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.11` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.2` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.3` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.4` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.5` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.6` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.7` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.8` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-09.9` **BLOCKED** - No consented plant recording with reviewed frame-level annotations supplied
- `SEC-10.1` **BLOCKED** - No digital-twin interface or vendor SDK made available
- `SEC-10.2` **BLOCKED** - No digital-twin interface or vendor SDK made available
- `SEC-10.8` **BLOCKED** - No digital-twin interface or vendor SDK made available
- `SEC-11.1` **BLOCKED** - No digital-twin interface or vendor SDK made available
- `SEC-12.11` **BLOCKED** - Delivery channel and team acknowledgement not available in this environment; no delivery has occurred
- `SEC-12.2` **BLOCKED** - Site/IT camera inventory not supplied to this environment

## 8. Next site inputs needed

- Site/IT camera inventory: camera ID, physical location, IP or hostname, RTSP port, stream path, access path, for each candidate (unblocks SEC-01.1-1.8 and SEC-12.2).
- Network reachability from a host on the plant camera VLAN, or an SSH bastion with a private key and a verified known_hosts entry (unblocks SEC-01.3-1.4, SEC-02.2-2.3, SEC-02.5, SEC-08.7).
- Camera credentials entered only through the private setup page, never in a prompt, log or report.
- Site camera intrinsics/extrinsics and a measured ground-plane calibration (unblocks the metric projection and anything depending on SEC-04.2).
- Identification of the PoC machine and hazard zone on site (unblocks SEC-04.1, SEC-11.1, SEC-10.1-10.2).
- Consented plant recordings with frame-level reviewed annotations covering outside, entering, remaining, leaving, multiple people and varied lighting (unblocks SEC-09.1-9.11 and SEC-05.7-5.9, SEC-06.7).
- Digital-twin interface or vendor SDK details (unblocks SEC-10.8 and the machine identity work in SEC-10.1-10.2, SEC-11.1-11.2).
- A delivery channel and acknowledgement for the documentation package; delivery is currently recorded as not having occurred.

## 9. Statement of limits

- No plant camera, no plant network route and no site calibration were available, so every site-dependent item is BLOCKED rather than passed.
- Restored repository recordings, the disposable RTSP fixture and the public Roboflow dataset are demonstration or unit evidence. They demonstrate mechanism and model behaviour; they do not establish detection accuracy at the plant or any site safety result.
- The bundled detectors are COCO-80 models with no excavator class; measured excavator recall is 0.20. Heavy-machinery coverage on any site footage is therefore expected to be limited until a domain detector is trained.
- The public dataset is partially labelled, so image-level presence metrics are more reliable than box-level metrics for person; both are reported in the detector evidence.
- No headless browser was available, so alert legibility (SEC-08.8) and the visual 3D state change (SEC-11.7) could not be visually confirmed and are recorded as NOT TESTED.
- The SEC-09 items 5 and 6 are duplicated in the source card. Both are retained and evaluated separately rather than reinterpreted.
- This is an advisory demonstration PoC, not an approved physical safety control.

> This PoC is an advisory demonstration, not an approved physical safety control. Its alerts are not a certified protection system and its measurements are not site results.
