"""Build the SEC-01..SEC-12 evidence record for the sprint validation harness.

One-off generator for the evidence JSON consumed by scripts/sprint_validation.py.
Kept beside the evidence it produces so the record can be regenerated and audited.
"""
import json
from pathlib import Path

DATE = "2026-09-29"
ENV = "Bazzite 44 (Linux 7.2.7-ogc1.1.fc44), 16-core AMD Ryzen 7 5700X, RTX 3060 present but torch is the CPU build"
SU = "SOURCE/UNIT"
DR = "DEMO/RECORDING"
SR = "SYNTHETIC RTSP"
PS = "PHYSICAL SITE CAMERA"

NO_INVENTORY = "Site/IT camera inventory not supplied to this environment"
NO_SITE_NET = "No reachability from this host to the plant camera network (camera on a site VLAN)"
NO_PLANT = "No consented plant recording with reviewed frame-level annotations supplied"
NO_CALIB = "No site camera intrinsics/extrinsics and ground-plane calibration for a plant camera"
NO_TWIN = "No digital-twin interface or vendor SDK made available"

items = []


def add(i, verdict, cls, method, expected, observed, reference, reason=None, blocked_on=None):
    record = {
        "id": i,
        "verdict": verdict,
        "evidence_class": cls,
        "method": expected and "" or "",
        "expected": expected,
        "observed": observed,
        "reference": reference,
    }
    record["method"] = method
    if blocked_on:
        record["blocked_on"] = blocked_on
    if reason:
        record["reason"] = reason
    items.append(record)


# ---------------------------------------------------------------- SEC-01
add("SEC-01.1", "BLOCKED", PS,
    "Query the survey store and site inventory for the camera list",
    "A list of cameras available to the project",
    "The application exposes the survey workflow, but the store is empty: 0 records, no selection, sec01_status.complete=false",
    "GET /api/v1/setup/survey on 18080 and 18083; log ui-routes.log", blocked_on=NO_INVENTORY)
add("SEC-01.2", "BLOCKED", PS,
    "Cross-check inventory entries against a physical site walkdown",
    "Physical location of each camera",
    "The setup form accepts a location field and the survey record stores it, but no camera entry exists to verify",
    "src/api/camera_survey.py; deploy/CAMERA_SURVEY.md", blocked_on=NO_INVENTORY)
add("SEC-01.3", "BLOCKED", PS,
    "Attempt TCP reachability to a plant camera endpoint",
    "IP address or access endpoint of each camera",
    "TCP reachability probing exists (_tcp_check) but no plant endpoint is known to this host",
    "src/api/connection_check.py; RTSP smoke shows the mechanism only", blocked_on=NO_SITE_NET)
add("SEC-01.4", "BLOCKED", PS,
    "Open the plant camera RTSP stream and decode a frame",
    "Verified access to each video stream",
    "A disposable RTSP fixture decoded and streamed successfully, which validates the mechanism but not any plant stream",
    "deploy/validation/deepseek-harness-2026-09-29/rtsp-smoke-cpu.log (RTSP_STREAMING_OK)", blocked_on=NO_SITE_NET)
add("SEC-01.5", "BLOCKED", PS,
    "Validate a plant camera for proximity detection",
    "Identified plant camera with technically validated video access",
    "No plant camera has been supplied; selection is inventory-gated and rejects a camera without location/host/port/path",
    "src/api/camera_survey.py select(); tests/test_camera_survey.py", blocked_on=NO_INVENTORY)
add("SEC-01.6", "BLOCKED", PS,
    "Measure resolution and quality on the plant camera",
    "Image resolution and quality validated for the selected camera",
    "Objective quality screening (min 640x480, sharpness, brightness, contrast) is implemented and exercised on restored recordings only",
    "src/api/connection_check.py; deploy/DETECTION_CALIBRATION.md", blocked_on=NO_INVENTORY)
add("SEC-01.7", "BLOCKED", PS,
    "Record a connectivity block observed while surveying a plant camera",
    "Network or connectivity blockers recorded",
    "The blocker mechanism is implemented and unit-tested (camera_open_failed, connection_refused, ssh_*), but no plant survey was performed so no real blocker was recorded",
    "src/api/connection_check.py _classify_error; tests/test_connection_check.py", blocked_on=NO_INVENTORY)
add("SEC-01.8", "BLOCKED", PS,
    "Read the selected camera from the survey report",
    "Selected camera documented",
    "The report renders location, host, port, stream path, resolution, quality and disposition, but no camera is selected",
    "GET /api/v1/setup/survey/report.md returns an empty selection", blocked_on=NO_INVENTORY)

# ---------------------------------------------------------------- SEC-02
add("SEC-02.1", "PASS", SR,
    "Run the disposable RTSP integration runner against the tested image",
    "Environment receives camera video and runs CV processing",
    "Real H.264/TCP decode, YOLO detection, telemetry API, disconnect, reconnect and shutdown all succeeded on CPU",
    "rtsp-smoke-cpu.log: RTSP_INTEGRATION_OK, exit 0")
add("SEC-02.2", "BLOCKED", PS,
    "Point the setup at the plant camera stream",
    "Configured access to the camera stream",
    "Setup accepts structured host/port/path plus SSH tunnel fields; the RTSP fixture path is proven, the plant path is not reachable",
    "src/api/camera_setup.py; rtsp-smoke-setup-cpu.log: RTSP_INTEGRATION_OK", blocked_on=NO_SITE_NET)
add("SEC-02.3", "BLOCKED", PS,
    "Stream from the plant camera and measure end-to-end latency",
    "Real-time video processing tested",
    "Real-time decode, tracking and detection ran against a disposable RTSP server with disconnect/reconnect, which is not a plant stream",
    "rtsp-smoke-cpu.log: RTSP_DISCONNECT_OK, RTSP_RECONNECT_OK", blocked_on=NO_SITE_NET)
add("SEC-02.4", "PASS", SU,
    "Inspect the pinned dependency set and the deployment guide",
    "Dependencies documented",
    "Pinned runtime lock plus documented operating constraints (single worker, reverse proxy, non-root, read-only root, no credentials in URLs) are present",
    "requirements-runtime.lock; deploy/PRODUCTION.md")
add("SEC-02.5", "BLOCKED", PS,
    "Measure processing performance on the intended single-camera host",
    "Initial processing performance validated",
    "Measured 62.88 ms median CPU inference for the calibrated detector and a median 6.03 ms frame serve for restored media, but no plant camera stream exists to measure end to end",
    "detector-summary.txt; docker-setup-smoke.log DEMO_API_MEASUREMENT", blocked_on=NO_SITE_NET)

# ---------------------------------------------------------------- SEC-03
add("SEC-03.1", "PASS", SU,
    "Measure person detection on the 398 labelled images with ground truth",
    "Image recognition identifies workers/people in the field of view",
    "Person recall 0.9275 (138 boxes); image-level presence precision 0.8721, recall 0.9845 over 398 labelled images",
    "detector-summary.txt; detector_evaluation.json; scripts/evaluate_detector.py")
add("SEC-03.2", "PASS", SU,
    "Record the selected model identity and class mapping",
    "A person-detection model is selected",
    "yolov8n.pt (COCO-80) is selected, hash-recorded, and its person class maps to the internal WORKER class",
    "sha256 f59b3d83...; src/perception/detector.py SENTINEL_CLASS_MAPPING")
add("SEC-03.3", "PASS", SR,
    "Run the model continuously against a live RTSP stream",
    "Model integrated with the camera stream",
    "The detector ran continuously over a decoded RTSP stream and produced telemetry until disconnect and reconnect",
    "rtsp-smoke-cpu.log: RTSP_STREAMING_OK, RTSP_DEVICE_OK cpu")
add("SEC-03.4", "PASS", DR,
    "Draw the served detection boxes on the served frame and inspect the overlay",
    "Each detected person is shown visually in the image",
    "Camera panel paints the decoded frame and a box per detection; serving a restored frame and drawing its own boxes produced exact alignment (calibration scale error 0.0)",
    "src/dashboard/assets/hub.js draw(); alignment verified on a served frame during the calibration work")
add("SEC-03.5", "PASS", DR,
    "Count detections in multi-person frames of the restored recordings",
    "Multiple people in one frame validated",
    "Up to 12 workers detected in a single frame of a restored recording; a 5-person frame was inspected and each detection was correctly placed. Evidence is recorded footage, not plant imagery",
    "detector_evaluation.json videos[] mean_workers_per_frame; alignment frame inspection")
add("SEC-03.6", "NOT TESTED", SU,
    "(not run) compare detections across controlled lighting conditions",
    "Behaviour under different lighting validated",
    "No lighting-variation fixture was constructed and no stratified-by-illumination breakdown was produced, so the effect of lighting is unquantified",
    "deploy/DETECTION_CALIBRATION.md records brightness/contrast thresholds but no lighting study",
    reason="Check was available but was not run: no lighting-conditioned subset was prepared")
add("SEC-03.7", "PASS", SU,
    "Publish the measured detection results and per-class recall",
    "Test results recorded",
    "Full report written with box and image-level metrics, per-ground-truth-class recall, coverage and calibration error",
    "data/reports/detector_evaluation.json; deploy/DETECTION_CALIBRATION.md")
add("SEC-03.8", "PASS", SU,
    "Check the documented limitations against measured behaviour",
    "Model limitations documented",
    "Excavator recall 0.20 versus person 0.93 is documented with its cause (COCO-80 has no excavator class) and the training step needed to close it",
    "deploy/DETECTION_CALIBRATION.md section 4")

# ---------------------------------------------------------------- SEC-04
add("SEC-04.1", "BLOCKED", PS,
    "Select a plant machine and hazard zone for the PoC",
    "Machine and zone selected for the first PoC",
    "Demo scenarios carry illustrative envelopes, but no plant machine or zone has been identified for the PoC",
    "src/api/demo_service.py KNOWN_SCENARIO_CONFIGS (marked illustrative)", blocked_on=NO_INVENTORY)
add("SEC-04.2", "BLOCKED", PS,
    "Mark the hazard area on plant imagery",
    "Hazard area identified visually",
    "No plant imagery or camera view of a hazard area is available to mark",
    "No plant image in the environment", blocked_on=NO_PLANT)
add("SEC-04.3", "FAIL", SU,
    "Inspect what the camera panel paints and where hazard polygons are rendered",
    "A virtual zone drawn over the camera image",
    "The camera panel paints only the decoded frame and detection boxes. Hazard polygons are rendered only in the separate 3D twin scene; no zone overlay exists on the camera image, which the brief states is not sufficient",
    "src/dashboard/assets/hub.js: draw() has no polygon path; polygon rendering only at the createTwin hazardGroup",
    reason="Required behaviour absent: the camera image has no zone overlay")
add("SEC-04.4", "PASS", SU,
    "Load a manifest with a configured exclusion polygon",
    "Zone boundaries configurable",
    "Envelopes are loaded from the active manifest (polygon_metric_epsg3857) with ttc_multiplier and exempt_entities, and demo scenarios ship envelopes",
    "src/edge/conflict_engine.py load_manifest_envelopes; data/manifests/active_manifest.json")
add("SEC-04.5", "FAIL", SU,
    "Search for zone entry/exit state transitions in the risk pipeline",
    "Entry into and exit from the zone identified",
    "The zone polygon is used only as a containment test inside evaluate_pair_risk (poly.covers). No entry, continued-presence or exit transition is tracked, and the only state machine is risk escalation",
    "src/edge/conflict_engine.py; no entry/exit transition exists",
    reason="Required behaviour absent: no entry/exit transition tracking exists")
add("SEC-04.6", "NOT TESTED", SU,
    "(not run) reconfigure the envelope polygon at several positions and compare",
    "Behaviour for different zone positions tested",
    "Zone geometry is configurable, but no position-variation comparison was executed, so the behaviour under different placements is unverified",
    "load_manifest_envelopes exists; no variation test in tests/",
    reason="Check was available but was not run: no zone-position variation test was written")
add("SEC-04.7", "PASS", SU,
    "Read the implemented zone rule and its specification",
    "Rule used is documented",
    "The rule is implemented in conflict_engine (containment raises the separation radius and TTC multiplier, exempt entities are ignored) and specified in agent.md. The worker-to-machinery proximity ladder and the PPE colour rule are now also restated in the deployment guides, with the DANGER radius tied to the engine's own r_col",
    "src/edge/conflict_engine.py evaluate_pair_risk; agent.md; deploy/SAFETY_EVENT_CONTRACT.md")

# ---------------------------------------------------------------- SEC-05
add("SEC-05.1", "PASS", SU,
    "Read the proximity criterion in the risk engine",
    "Proximity criterion defined",
    "Collision radius r_col = max(min_separation_distance, footprint_radius_a + footprint_radius_b), raised inside a zone by the envelope separation, with time-to-collision and mode-mass probability thresholds. A worker-to-machinery ladder (DANGER at or inside r_col = 3.3 m, NEAR to 8.0 m, SAFE beyond, NO_MACHINE when no machinery is in frame) is also defined for restored recordings; the 3.3 m band is the engine's own radius, the 8.0 m band is an unvalidated engineering default",
    "src/edge/conflict_engine.py evaluate_pair_risk; src/api/media_tracks.py; tests/test_conflict_engine.py; tests/test_ppe_proximity_and_separation.py")
add("SEC-05.2", "PASS", SU,
    "Trace detections into the footpoint used for risk",
    "Detected person position used",
    "Detector boxes are converted to a ground footpoint and carried as the tracked position mu_x/mu/y used for containment and separation. Restored recordings now do the same: a per-recording fitted ground plane maps the box footpoint to metric coordinates, and that metric position drives both zone occupancy and the proximity grade",
    "src/edge/edge_runtime.py; src/edge/conflict_engine.py; src/api/media_tracks.py")
add("SEC-05.3", "PASS", SU,
    "Test the containment association between person and zone",
    "Person associated with the hazard zone",
    "The person position and the zone are tested against the same CANONICAL_ZONE constant, and the polygon drawn in the 3D canvas is positioned and scaled from that same packet field, so the drawn rectangle and the tested rectangle cannot diverge. The association is real; the zone's depth band is fitted to each recording rather than surveyed, so it is a working-area test and not a validated hazard boundary",
    "src/api/media_tracks.py in_canonical_zone; src/dashboard/assets/hub.js trenchMesh.position/scale from packet.zone; tests/test_3d_pose_and_zone_calibration.py")
add("SEC-05.4", "FAIL", SU,
    "Search for an entry transition into the zone",
    "Entry into the zone detected",
    "No entry event is produced. Zone membership is re-evaluated per frame with no prior state, so an entry cannot be distinguished from steady occupancy",
    "src/edge/conflict_engine.py has no entry transition",
    reason="Required behaviour absent: zone membership is stateless per frame")
add("SEC-05.5", "FAIL", SU,
    "Search for a continued-presence state",
    "Continued presence in the zone detected",
    "No presence state is carried between frames; only risk-level escalation and de-escalation are debounced",
    "src/edge/conflict_engine.py Debouncer only",
    reason="Required behaviour absent: no presence state is carried between frames")
add("SEC-05.6", "FAIL", SU,
    "Search for an exit transition from the zone",
    "Exit from the zone detected",
    "No exit event is produced for the same reason as entry: occupancy is stateless per frame",
    "src/edge/conflict_engine.py has no exit transition",
    reason="Required behaviour absent: no exit transition exists")
add("SEC-05.7", "BLOCKED", PS,
    "Play back a plant scenario where the worker stays clear of the machine",
    "Safe scenario tested",
    "A diverging-path safe case is covered by unit tests and restored recordings exist, but the plant scenario required by the card was not available",
    "tests/test_conflict_engine.py test_diverging_safe_paths", blocked_on=NO_PLANT)
add("SEC-05.8", "BLOCKED", PS,
    "Play back a plant scenario where the worker approaches the machine",
    "Risk scenario tested",
    "An imminent-collision risk case is covered by unit tests, but the plant scenario required by the card was not available",
    "tests/test_conflict_engine.py test_imminent_head_on_collision", blocked_on=NO_PLANT)
add("SEC-05.9", "BLOCKED", PS,
    "Record the safe and risk scenario results",
    "Results of the scenario tests recorded",
    "Contingent on 5.7 and 5.8, which are blocked; no plant scenario result can be recorded",
    "depends on SEC-05.7 and SEC-05.8", blocked_on=NO_PLANT)

# ---------------------------------------------------------------- SEC-06
add("SEC-06.1", "PASS", SU,
    "Read the normal level in the risk engine",
    "Safe state defined",
    "NORMAL_LEVEL_0 is returned when the pair is clear or when the person is an exempt entity inside a zone",
    "src/edge/conflict_engine.py")
add("SEC-06.2", "PASS", SU,
    "Read the advisory level in the risk engine",
    "Attention state defined",
    "ADVISORY_LEVEL_1 is a defined band (fixed 5.0 s reference and 0.20 probability)",
    "src/edge/conflict_engine.py level thresholds")
add("SEC-06.3", "PASS", SU,
    "Read the warning and critical levels",
    "Risk state defined",
    "WARNING_LEVEL_2 and CRITICAL_LEVEL_3 are defined bands. The engine exposes four internal levels where the card names three business states; see SEC-06.8 for the missing documented mapping",
    "src/edge/conflict_engine.py level_map")
add("SEC-06.4", "PASS", SU,
    "Read the threshold conditions for each level",
    "Condition for each state defined",
    "Each level has a numeric condition: time-to-collision threshold scaled by the envelope multiplier plus a mode-mass probability threshold (critical uses p_threshold, warning 0.6x, advisory 5.0 s / 0.20)",
    "src/edge/conflict_engine.py thresholds")
add("SEC-06.5", "PASS", SU,
    "Trace proximity and forecast into the level decision",
    "Proximity associated with the risk level",
    "Separation radius and time-to-collision of the forecast modes produce p_col, which selects the level; zone multiplier and exemptions modify the inputs",
    "src/edge/conflict_engine.py evaluate_pair_risk")
add("SEC-06.6", "PASS", SU,
    "Run the debouncer transition tests",
    "Changes between states tested",
    "Escalation and de-escalation are asserted with the frame debounce, plus imminent-collision, diverging, mixture-mode and late-peak cases",
    "tests/test_conflict_engine.py test_debouncer_escalation_and_deescalation (6 tests, all ok)")
add("SEC-06.7", "BLOCKED", PS,
    "Replay plant footage and compare level with reality",
    "Behaviour on real video validated",
    "Levels were exercised on synthetic and recorded material only; no plant footage was available to compare level decisions with reality",
    "rtsp-smoke-cpu.log (synthetic); restored recordings", blocked_on=NO_PLANT)
add("SEC-06.8", "FAIL", SU,
    "Search the repository for a mapping of the four internal levels to Safe/Attention/Risk",
    "Risk rules documented, including the mapping to the business states",
    "No document maps NORMAL/ADVISORY/WARNING/CRITICAL to the Safe/Attention/Risk states the card defines, and four internal levels do not map one-to-one onto three business states",
    "grep over deploy/ and README.md found no mapping; only agent.md mathematics",
    reason="Required documentation absent: no mapping of the four internal levels to Safe/Attention/Risk")

# ---------------------------------------------------------------- SEC-07
add("SEC-07.1", "FAIL", SU,
    "Look for a schema describing the safety event",
    "Event structure created",
    "No event schema exists. The only incident model is an adjudication verdict (source-camera free text). /api/v1/incidents/publish accepts an untyped dict and relays it",
    "src/api/app.py publish_edge_incident(incident_packet: dict); src/schemas/contracts.py",
    reason="Required behaviour absent: no event schema exists; the relay takes an untyped dict")
add("SEC-07.2", "FAIL", SU,
    "Check for a camera field in any emitted event",
    "Event records the source camera",
    "The relay forwards whatever the caller sends; nothing populates a camera identifier from the pipeline",
    "src/api/app.py publish_edge_incident",
    reason="Required behaviour absent: nothing populates a camera identifier on an event")
add("SEC-07.3", "FAIL", SU,
    "Check for a zone or machine field in any emitted event",
    "Event records the zone or machine",
    "No zone or machine identifier is attached to any emitted event",
    "no event producer in src/",
    reason="Required behaviour absent: no zone or machine identifier is attached to any event")
add("SEC-07.4", "FAIL", SU,
    "Check for a timestamp in any emitted event",
    "Event records date and time",
    "Telemetry carries captured_at inside a frame packet, but no event is produced, so no event timestamp exists",
    "no event producer in src/",
    reason="Required behaviour absent: no event carries a timestamp")
add("SEC-07.5", "FAIL", SU,
    "Check for a risk level field in any emitted event",
    "Event records the risk level",
    "The per-frame packet carries a debounced alarm and per-pair risk, but no event object records the level at the moment of change",
    "no event producer in src/",
    reason="Required behaviour absent: no event records the risk level at transition")
add("SEC-07.6", "FAIL", SU,
    "Check for an event type field in any emitted event",
    "Event records the event type",
    "No event type field exists; the relay passes an opaque dict through",
    "src/api/app.py publish_edge_incident",
    reason="Required behaviour absent: no event type field exists")
add("SEC-07.7", "FAIL", SR,
    "Drive the live pipeline and watch for an event on a risk transition",
    "Automatic event generation tested",
    "Nothing generates an event from a risk transition. Zone occupancy transitions now do emit ZONE_ENTRY, ZONE_OCCUPANCY and ZONE_EXIT automatically for restored recordings, but the risk-level transition still emits nothing: the only writer of incident-like records appends to an active-learning queue from an explicit adjudication call",
    "src/graph/adjudication_pipeline.py; /api/v1/incidents/publish is a caller-driven relay; src/api/media_tracks.py emits zone events only",
    reason="Contrary result: driving the live pipeline produced no event on a risk transition")
add("SEC-07.8", "FAIL", SU,
    "Attempt to validate the event format",
    "Event format validated",
    "There is no event format to validate; the relay accepts any JSON object without schema checks",
    "POST /api/v1/incidents/publish with an arbitrary dict returned BROADCAST_SUCCESS",
    reason="Required behaviour absent: there is no event format to validate")
add("SEC-07.9", "FAIL", SU,
    "Search the documentation for an event contract",
    "Event contract documented",
    "No event contract is documented in README.md or deploy/",
    "grep 'event contract|event schema|event structure' over deploy/ and README.md returned nothing",
    reason="Required documentation absent: no event contract is documented")

# ---------------------------------------------------------------- SEC-08
add("SEC-08.1", "PASS", DR,
    "Inspect the camera panel rendering for a detected person",
    "Detected person shown",
    "Each detection is drawn as a labelled box over the decoded frame, and served frames carry per-frame boxes",
    "src/dashboard/assets/hub.js draw(); live frame packets carry detections")
add("SEC-08.2", "FAIL", SU,
    "Inspect the camera panel for a hazard zone overlay",
    "Hazard zone shown",
    "The camera panel shows no zone. Hazard geometry is drawn only in the 3D twin, and a restored recording carries no hazard envelope at all",
    "hub.js draw() has no polygon path; restored packets have hazards=[]",
    reason="Required behaviour absent: the camera panel shows no hazard zone")
add("SEC-08.3", "PASS", SU,
    "Inspect the state indicators on the camera panel",
    "Current state shown",
    "State is shown by the alert badge, the telemetry tag, the TTC/collision metrics and the forecast status; with no camera these read UNAVAILABLE",
    "src/dashboard/index.html system-alert-badge, cam-telemetry-tag, metric-ttc, gnn-status")
add("SEC-08.4", "PASS", SU,
    "Inspect how a risk situation is highlighted",
    "Risk situation highlighted",
    "Risk is highlighted by the debounced alert badge, the time-to-collision and collision-mode metrics, and the red hazard geometry in the twin",
    "src/dashboard/assets/hub.js display(); tests/test_conflict_engine.py escalation cases")
add("SEC-08.5", "FAIL", SU,
    "Search the camera panel for a machine identifier",
    "Machine identifier shown",
    "No machine identifier exists anywhere in the camera panel; the DOM has no element for it and the detector emits class ids, not machine identities",
    "grep 'machine' src/dashboard/index.html returns no matches",
    reason="Required behaviour absent: no machine identifier exists in the camera panel")
add("SEC-08.6", "FAIL", SU,
    "Search the camera panel for a timestamp",
    "Timestamp shown",
    "No timestamp element exists in the camera panel; captured_at exists only inside the frame packet and is never displayed",
    "grep 'timestamp|captured_at' src/dashboard/index.html returns no matches",
    reason="Required behaviour absent: no timestamp element exists in the camera panel")
add("SEC-08.7", "BLOCKED", PS,
    "Trigger an alert on the plant camera stream",
    "Real-time alert tested",
    "Alerts were exercised on a disposable RTSP stream and recorded material; no plant camera stream exists to alert on",
    "rtsp-smoke-cpu.log", blocked_on=NO_SITE_NET)
add("SEC-08.8", "NOT TESTED", PS,
    "(not run) assess alert legibility on a real display",
    "Alert legibility validated",
    "No headless browser or display is available in this environment, so legibility could not be assessed on rendered output; only static DOM inspection was possible",
    "no chromium, puppeteer or playwright available",
    reason="Check was available but was not run: no browser rendering available")

# ---------------------------------------------------------------- SEC-09
for n, text in [
    (1, "worker outside the zone"), (2, "worker entering the zone"), (3, "worker remaining in the zone"),
    (4, "worker leaving the zone"), (5, "more than one person"),
    (6, "more than one person (duplicated in the source card)"), (7, "different lighting conditions"),
]:
    add(f"SEC-09.{n}", "BLOCKED", PS,
        "Replay a consented plant recording and score detections against reviewed annotations",
        f"Test a {text}",
        "No consented plant recording was supplied. Restored repository recordings are not plant evidence and carry no frame-level review",
        f"no plant recording in the environment; {NO_PLANT}", blocked_on=NO_PLANT)
add("SEC-09.8", "BLOCKED", PS,
    "Tally false positives against reviewed plant annotations",
    "False positives recorded",
    "False positives can be measured (28 person-presence false-alarm images) but only on the public Roboflow dataset, which is not the plant footage this card requires",
    "detector-summary.txt image-level presence; " + NO_PLANT, blocked_on=NO_PLANT)
add("SEC-09.9", "BLOCKED", PS,
    "Tally false negatives against reviewed plant annotations",
    "False negatives recorded",
    "False negatives can be measured (3 person-presence misses) but only on the public dataset, not the plant footage this card requires",
    "detector-summary.txt image-level presence; " + NO_PLANT, blocked_on=NO_PLANT)
add("SEC-09.10", "BLOCKED", PS,
    "Record performance problems observed on plant footage",
    "Performance problems recorded",
    "Detector limitations are recorded from public data (excavator recall 0.20, 51 missed heavy-machinery images); plant-specific performance problems are unknown",
    "deploy/DETECTION_CALIBRATION.md; " + NO_PLANT, blocked_on=NO_PLANT)
add("SEC-09.11", "BLOCKED", PS,
    "Consolidate the plant validation results",
    "Results consolidated",
    "Consolidation is contingent on 9.1-9.10, all of which are blocked without plant recordings and reviewed annotations",
    "depends on SEC-09.1 to SEC-09.10", blocked_on=NO_PLANT)

# ---------------------------------------------------------------- SEC-10
add("SEC-10.1", "BLOCKED", PS,
    "Identify the plant machine in the digital twin",
    "Corresponding machine identified in the twin",
    "The twin renders a generic ground plane with track markers; no plant machine model or machine identity is available",
    "src/dashboard/assets/hub.js createTwin GridHelper; " + NO_TWIN, blocked_on=NO_TWIN)
add("SEC-10.2", "BLOCKED", PS,
    "Map a plant camera to a machine and zone",
    "Camera mapped to machine and zone",
    "No camera-to-machine mapping exists; the survey model stops at camera inventory and a selected camera",
    "src/api/camera_survey.py; " + NO_TWIN, blocked_on=NO_TWIN)
add("SEC-10.3", "FAIL", SU,
    "Inspect the transports offered to an external consumer",
    "Communication structure defined for the twin",
    "There is a caller-driven WebSocket relay and an optional in-app MQTT telemetry client, but no defined external-twin message structure, handshake or topic contract",
    "src/api/app.py publish_edge_incident; src/edge/edge_runtime.py mqtt (optional, off by default)",
    reason="Required behaviour absent: no external-twin message structure, handshake or topic contract")
add("SEC-10.4", "FAIL", SU,
    "Look for a specification of how a twin would receive an event",
    "How the twin receives the event defined",
    "No such specification exists, and there is no event to deliver (see SEC-07.1)",
    "no event contract documented; " + NO_TWIN,
    reason="Required behaviour absent: no specification of twin event delivery, and no event exists")
add("SEC-10.5", "PASS", SU,
    "Inspect the twin overlay legend and colours",
    "Visual representation of risk defined",
    "Risk is represented by a legend and fixed colours: red configured hazard, blue worker tracks, amber equipment, plus a red semi-transparent zone shape",
    "src/dashboard/index.html twin-overlay-info; hub.js hazard mesh colour 0xef4444")
add("SEC-10.6", "FAIL", SU,
    "Look for machine and zone state definitions",
    "Machine and zone states defined",
    "Zone state is implied by the red overlay, but no machine state is defined anywhere and the twin has no machine representation to carry one",
    "no machine state in src/; " + NO_TWIN,
    reason="Required behaviour absent: no machine state is defined and the twin has no machine")
add("SEC-10.7", "PASS", SU,
    "Exercise the working Three.js prototype in the running app",
    "Visual mockup or prototype created",
    "A working Three.js prototype renders on the metric ground plane with worker tracks, forecast paths and the hazard polygon, driven by the live hub",
    "src/dashboard/assets/hub.js createTwin; /api/v1/hub/demo/<scenario>/frames/<n> returns 200")
add("SEC-10.8", "BLOCKED", PS,
    "Validate feasibility with the twin vendor",
    "Feasibility of future integration validated",
    "Feasibility cannot be assessed without the digital-twin interface, SDK or vendor schema",
    NO_TWIN, blocked_on=NO_TWIN)

# ---------------------------------------------------------------- SEC-11
add("SEC-11.1", "BLOCKED", PS,
    "Select a plant machine for the 3D prototype",
    "PoC machine selected",
    "Demo scenarios name an excavator scenario but no plant machine is identified, and the twin holds no machine model",
    "src/api/demo_service.py; " + NO_TWIN, blocked_on=NO_TWIN)
add("SEC-11.2", "FAIL", SU,
    "Inspect the 3D scene contents",
    "Simplified 3D model of the machine and zone used or created",
    "The scene contains a ground grid, track markers and a hazard polygon. No machine geometry exists, so the machine cannot be represented",
    "src/dashboard/assets/hub.js createTwin: GridHelper + markers only",
    reason="Required behaviour absent: the 3D scene contains no machine geometry")
add("SEC-11.3", "PASS", SU,
    "Confirm the zone geometry is rendered in 3D",
    "Safety zone represented",
    "The configured zone polygon is rendered as a semi-transparent shape on the metric ground plane",
    "hub.js hazardGroup ShapeGeometry; twin-overlay-info legend")
add("SEC-11.4", "PASS", SU,
    "Confirm a worker is represented in 3D",
    "Worker represented",
    "Tracked workers are rendered as markers and their forecast paths as lines, coloured by class",
    "hub.js createTwin update(); track meshes")
add("SEC-11.5", "FAIL", SU,
    "Look for a safe-state representation in the 3D scene",
    "Safe state represented in 3D",
    "The 3D scene has no state representation: the hazard polygon is always drawn identically and no safe-state geometry or colour change exists. State is only conveyed in the 2D panel",
    "hub.js createTwin renders no state change",
    reason="Required behaviour absent: the 3D scene has no safe-state representation")
add("SEC-11.6", "FAIL", SU,
    "Look for a risk-state representation in the 3D scene",
    "Risk state represented in 3D",
    "The 3D scene does not change with risk level; forecast paths are drawn in the same style regardless of risk and the alarm lives in the 2D panel",
    "hub.js createTwin update() has no risk-driven styling",
    reason="Required behaviour absent: the 3D scene does not change with risk level")
add("SEC-11.7", "NOT TESTED", SU,
    "(not run) drive a state change in the browser and confirm the 3D scene reacts",
    "Real-time state change simulated",
    "The twin update path is exercised by unit tests and by served frame packets, but no browser rendering was available to confirm a visible state change in the live 3D scene",
    "no headless browser available",
    reason="Check was available but was not run: no browser rendering available")
add("SEC-11.8", "PASS", SU,
    "Exercise the demonstrable path end to end",
    "Demonstration prepared",
    "A demonstrable path exists: the hub serves restored recordings with calibrated detections, the twin renders tracks and zones, and the setup page drives camera configuration",
    "GET / and /setup return 200; /api/v1/hub/demo/... returns frames with detections")

# ---------------------------------------------------------------- SEC-12
add("SEC-12.1", "PASS", SU,
    "Review the architecture documentation",
    "Architecture documented",
    "Architecture is documented at system and component level, including the edge physics layer, the kinematic/forecast chain and the agentic layer",
    "README.md; agent.md")
add("SEC-12.2", "BLOCKED", PS,
    "Name the camera used in the PoC",
    "Camera used documented",
    "No plant camera has been selected, so there is no camera to document",
    "GET /api/v1/setup/survey/report.md shows no selected camera", blocked_on=NO_INVENTORY)
add("SEC-12.3", "PASS", SU,
    "Check the recorded model identity and its measured behaviour",
    "Computer vision model documented",
    "Model file, hash, class mapping, measured precision/recall and the excavator limitation are recorded",
    "deploy/DETECTION_CALIBRATION.md; sha256 f59b3d83...")
add("SEC-12.4", "PASS", SU,
    "Locate the proximity rule documentation",
    "Proximity rules documented",
    "The proximity rule is specified in the repository's mathematical documentation, implemented in the risk engine, and now also restated in the deployment guides for operators, including the worker-to-machinery ladder and the fact that its NEAR band is an unvalidated default. The trench depth band is documented as fitted per recording rather than surveyed, and its unvalidated status is stated alongside it",
    "agent.md; src/edge/conflict_engine.py evaluate_pair_risk; deploy/SAFETY_EVENT_CONTRACT.md; deploy/VALIDATION.md")
add("SEC-12.5", "FAIL", SU,
    "Search the documentation for the risk level mapping",
    "Risk levels documented",
    "The four internal levels are not documented in terms of the card's Safe/Attention/Risk states, and no mapping between them exists in any document",
    "grep for a level-to-state mapping over deploy/ and README.md returned nothing",
    reason="Required documentation absent: internal levels are not documented against the card's business states")
add("SEC-12.6", "FAIL", SU,
    "Search the documentation for the event format",
    "Event format documented",
    "There is no event format to document, because no event structure exists (see SEC-07.1)",
    "no event schema or contract in src/ or deploy/",
    reason="Required documentation absent: there is no event format to document")
add("SEC-12.7", "PASS", SU,
    "Review the recorded test results and this validation",
    "Test results recorded",
    "164 Python tests, 17 browser tests and the three isolated integration runners are recorded with exact commands, exit codes and logs, together with the detector measurement",
    "deploy/validation/deepseek-harness-2026-09-29/*.log; deploy/VALIDATION.md")
add("SEC-12.8", "PASS", SU,
    "Review the recorded limitations",
    "Limitations recorded",
    "Recorded limitations include the COCO-only detector, the excavator recall gap, the partially labelled dataset, the absence of site evidence and the advisory-only status of the PoC",
    "deploy/DETECTION_CALIBRATION.md; this report section 9")
add("SEC-12.9", "PASS", SU,
    "Review the recorded problems and gaps",
    "Problems encountered recorded",
    "Problems are recorded with evidence: missing camera overlay, missing zone/machine/timestamp in alerts, absent event structure, absent entry/exit handling, the 384-entry cache eviction stall and the shared-model prewarm serialisation",
    "this report section 7")
add("SEC-12.10", "PASS", SU,
    "Review the defined next steps",
    "Next steps defined",
    "Next steps are defined in dependency order in this report, with the site inputs required to unblock each card",
    "this report section 8")
add("SEC-12.11", "BLOCKED", PS,
    "Deliver the documentation to the team and confirm receipt",
    "Documentation delivered to the team",
    "No delivery has occurred. This report and the deploy guides exist in the repository only; nothing has been sent to the project team and no delivery channel or acknowledgement is available from this environment",
    "no delivery channel available; " + NO_TWIN, blocked_on="Delivery channel and team acknowledgement not available in this environment; no delivery has occurred")

evidence = {
    "identity": {
        "date": DATE,
        "repository": "/var/home/cortex/Projects/SentinelZone-AI",
        "git_commit": "bc8e2c88d376575503e77351f1b123b13c547535 (main)",
        "working_tree": "79 entries (20 modified, 59 untracked) - uncommitted, so file hashes are the source identifier",
        "source_snapshot_app_py": "a9ec430069ffc75f5",
        "source_snapshot_hub_py": "ec697b6b70411a73f",
        "source_snapshot_media_py": "58dcea4784c07c96",
        "source_snapshot_media_detection_py": "275108860f2b1952",
        "source_snapshot_index_html": "aaed51dd6c770d8f",
        "model_yolov8n_pt": "sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36",
        "model_weights_yolo26n_pt": "sha256 9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef",
        "app_url_source": "http://127.0.0.1:18083",
        "app_url_docker": "http://127.0.0.1:18080",
        "docker_image": "sentinelzone-ai:demo-media",
        "docker_image_id": "sha256:9e2e1f2cb7732566320eaded13be54944f783b4f0ff98a0bdce41a0c16e20d3e",
        "docker_image_built": "2026-09-29T17:36:13+01:00",
        "older_image_not_used": "sentinelzone-ai:standalone-20260924 (built 2026-09-24) is stopped and was not used as evidence for the current app",
        "port_18081": "nothing listening; the previously used separate image is not running",
    },
    "environment": {
        "os": "Bazzite 44 (Linux 7.2.7-ogc1.1.fc44.x86_64)",
        "cpu": "AMD Ryzen 7 5700X, 16 logical cores",
        "gpu": "NVIDIA GeForce RTX 3060 present, driver 615.71.09; torch 2.14.0+cpu build, CUDA unavailable",
        "python": "3.11.16 (.venv-prod)",
        "node": "v26.8.1",
        "libraries": "opencv 4.11.0, torch 2.14.0+cpu, numpy 2.1.3, ultralytics 8.3.134",
        "health_18080": "HTTP 200 HEALTHY, monitoring_ready=false, device=unavailable",
        "ready_18080": "HTTP 503 {\"ready\":false,\"cameras\":[]}",
        "health_18083": "HTTP 200 HEALTHY, monitoring_ready=false, device=unavailable",
        "ready_18083": "HTTP 503 {\"ready\":false,\"cameras\":[]}",
        "live_monitoring_configured": "no - 0 cameras on both instances, no camera draft saved",
        "sec01_survey_state": "0 records, no selected camera, sec01_status.complete=false on both instances",
        "browser_automation": "none available (no chromium, puppeteer or playwright); no screenshots or browser console capture possible",
    },
    "commands": [
        {"command": ".venv-prod/bin/python scripts/run_tests.py", "exit": "0", "result": "164 tests, OK"},
        {"command": "node tests/hub.test.cjs", "exit": "0", "result": "6 passed, 0 failed"},
        {"command": "node tests/live_dashboard.test.cjs", "exit": "0", "result": "4 passed, 0 failed"},
        {"command": "node tests/media_panel.test.cjs", "exit": "0", "result": "7 passed, 0 failed"},
        {"command": "python scripts/docker_setup_smoke.py --image sentinelzone-ai:demo-media", "exit": "0", "result": "OFFLINE_HUB_OK, SETUP_INTEGRATION_OK"},
        {"command": "python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:demo-media", "exit": "0", "result": "RTSP_INTEGRATION_OK (stream, disconnect, reconnect, device, shutdown)"},
        {"command": "python scripts/docker_rtsp_smoke.py --image sentinelzone-ai:demo-media --setup", "exit": "0", "result": "RTSP_INTEGRATION_OK via the saved setup API"},
        {"command": "python scripts/docker_ssh_smoke.py --image sentinelzone-ai:demo-media --fixture sentinelzone-ai:ssh-test-fixture", "exit": "0", "result": "SSH_INTEGRATION_OK (key, strict host verification, loopback forward, reconnect, shutdown)"},
        {"command": ".venv-prod/bin/python scripts/evaluate_detector.py --video-frames 6", "exit": "0", "result": "398 labelled images measured; report written"},
    ],
    "items": items,
    "next_site_inputs": [
        "Site/IT camera inventory: camera ID, physical location, IP or hostname, RTSP port, stream path, access path, for each candidate (unblocks SEC-01.1-1.8 and SEC-12.2).",
        "Network reachability from a host on the plant camera VLAN, or an SSH bastion with a private key and a verified known_hosts entry (unblocks SEC-01.3-1.4, SEC-02.2-2.3, SEC-02.5, SEC-08.7).",
        "Camera credentials entered only through the private setup page, never in a prompt, log or report.",
        "Site camera intrinsics/extrinsics and a measured ground-plane calibration (unblocks the metric projection and anything depending on SEC-04.2).",
        "Identification of the PoC machine and hazard zone on site (unblocks SEC-04.1, SEC-11.1, SEC-10.1-10.2).",
        "Consented plant recordings with frame-level reviewed annotations covering outside, entering, remaining, leaving, multiple people and varied lighting (unblocks SEC-09.1-9.11 and SEC-05.7-5.9, SEC-06.7).",
        "Digital-twin interface or vendor SDK details (unblocks SEC-10.8 and the machine identity work in SEC-10.1-10.2, SEC-11.1-11.2).",
        "A delivery channel and acknowledgement for the documentation package; delivery is currently recorded as not having occurred.",
    ],
    "limits": [
        "No plant camera, no plant network route and no site calibration were available, so every site-dependent item is BLOCKED rather than passed.",
        "Restored repository recordings, the disposable RTSP fixture and the public Roboflow dataset are demonstration or unit evidence. They demonstrate mechanism and model behaviour; they do not establish detection accuracy at the plant or any site safety result.",
        "The bundled detectors are COCO-80 models with no excavator class; measured excavator recall is 0.20. Heavy-machinery coverage on any site footage is therefore expected to be limited until a domain detector is trained.",
        "The public dataset is partially labelled, so image-level presence metrics are more reliable than box-level metrics for person; both are reported in the detector evidence.",
        "No headless browser was available, so alert legibility (SEC-08.8) and the visual 3D state change (SEC-11.7) could not be visually confirmed and are recorded as NOT TESTED.",
        "The SEC-09 items 5 and 6 are duplicated in the source card. Both are retained and evaluated separately rather than reinterpreted.",
        "This is an advisory demonstration PoC, not an approved physical safety control.",
    ],
}

out = Path(__file__).with_name("sec01_12_evidence.json")
out.write_text(json.dumps(evidence, indent=2))
print(f"wrote {out} with {len(items)} item records")
