import time
import json
import logging
from typing import Callable, Dict, List, Any, Optional
import numpy as np
import torch
from scipy.optimize import linear_sum_assignment

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    mqtt = None
    HAS_MQTT = False

from src.edge.homography import HomographyProjector
from src.edge.tracker import MetricTrack
from src.edge.gatv2_model import WorkZoneSTGNN
from src.edge.conflict_engine import DynamicConflictEngine, AlertHysteresisDebouncer
from src.edge.zone_occupancy import ZoneOccupancyTracker
from src.schemas.safety_event import (LEVEL_ORDER, DetectionStatus, EventType, SafetyEventLog, build_event,
                                  business_state, to_risk_level)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SentinelEdgeRuntime")


def _level_rank(level: Optional[str]) -> int:
    """Ordinal of a severity level, for comparing two levels.

    Severity names are not lexicographically ordered ("NORMAL_LEVEL_0" sorts
    above "CRITICAL_LEVEL_3"), so levels must be compared by this rank.
    """
    try:
        return LEVEL_ORDER.index(to_risk_level(str(level)))
    except (ValueError, KeyError):
        return 0


class SentinelEdgeRuntime:
    """
    Integrated deterministic real-time loop running on NVIDIA Jetson AGX Orin.
    Processes detection bounding boxes, projects to ground metric space, maintains
    EKF tracks, executes spatio-temporal GNN trajectory forecasting, evaluates
    spatial conflicts against dynamic BIM manifests, debounces alerts, and actuates relays.
    """
    def __init__(self, homography_cfg: dict, gnn_checkpoint_path: Optional[str] = None, mqtt_host: Optional[str] = "localhost", device: Optional[str] = None, camera_profile: Optional[dict] = None, event_sink: Optional[Callable[[object], None]] = None):
        # 1. Projector
        self.projector = HomographyProjector(
            camera_matrix=np.array(homography_cfg["K"]),
            dist_coeffs=np.array(homography_cfg["dist"]),
            homography_matrix=np.array(homography_cfg["H"])
        )

        # 2. Spatio-Temporal GNN
        self.device = torch.device(device if device and device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
        if self.device.type == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            logger.info(f"SentinelEdgeRuntime initialized on GPU: {gpu_name} ({vram_gb:.1f} GB VRAM)")
        else:
            logger.info("SentinelEdgeRuntime initialized on CPU")

        self.gnn = WorkZoneSTGNN().to(self.device)
        if gnn_checkpoint_path:
            try:
                checkpoint = torch.load(gnn_checkpoint_path, map_location=self.device, weights_only=True)
                if camera_profile is not None:
                    from src.edge.checkpoint import validate_checkpoint
                    state_dict = validate_checkpoint(checkpoint, camera_profile)
                else:
                    state_dict = checkpoint.get("model_state_dict", checkpoint)
                self.gnn.load_state_dict(state_dict)
                logger.info(f"Loaded GNN checkpoint from {gnn_checkpoint_path}")
            except Exception as e:
                raise RuntimeError("Configured GNN checkpoint could not be loaded") from e
        self.gnn.eval()
        self.forecast_available = bool(gnn_checkpoint_path)
        self.last_timestamp = None

        # 3. Dynamic Tracking & Risk Engine
        self.tracks: Dict[int, MetricTrack] = {}
        self.next_track_id = 1
        self.conflict_engine = DynamicConflictEngine()
        self.debouncer = AlertHysteresisDebouncer()
        # Zone entry/presence/exit state and the structured safety event. The
        # risk engine itself is stateless per frame, so occupancy is tracked here.
        self.occupancy = ZoneOccupancyTracker()
        self.event_log = SafetyEventLog(sink=event_sink)
        profile = camera_profile or {}
        self.camera_id = str(profile.get("camera_id") or "UNKNOWN_CAMERA")
        self.site_id = str(profile.get("site_id") or "")
        # The scenario/demo envelopes are configuration, not surveyed site zones.
        self.zone_source = str(profile.get("zone_source") or "SITE_MANIFEST")
        self._last_min_ttc = -1.0
        self._last_p_col = 0.0
        # Identity of the manifest actually in force. When envelopes come from a
        # shift manifest, that manifest is authoritative for the site: the
        # configured camera site is only a fallback, because the original
        # repository's config and its active manifest name different sites.
        self.manifest_site_id = ""
        self.manifest_shift_id = ""
        self.manifest_match = ""
        self.frame_width = int(profile.get("width") or 0)
        self.frame_height = int(profile.get("height") or 0)

        # 4. MQTT Broker for Telemetry & Manifest
        self.mqtt_client = None
        if mqtt_host and HAS_MQTT and mqtt is not None:
            try:
                # Support both paho-mqtt v1 and v2 API
                try:
                    self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="SentinelZone_Edge_Orin")
                except AttributeError:
                    self.mqtt_client = mqtt.Client(client_id="SentinelZone_Edge_Orin")
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.connect(mqtt_host, 1883, 60)
                self.mqtt_client.subscribe("/sentinel/site_01/manifest")
                self.mqtt_client.loop_start()
                logger.info(f"Connected to MQTT broker at {mqtt_host}:1883")
            except Exception as e:
                logger.warning(f"MQTT connection to {mqtt_host}:1883 failed: {e}. Telemetry running offline.")

        self.last_alarm_state = "NORMAL_LEVEL_0"
        self.relay_triggered = False

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if "envelopes" in payload:
                self.conflict_engine.load_manifest_envelopes(payload["envelopes"])
                logger.info(f"Updated dynamic manifest with {len(payload['envelopes'])} spatial envelopes.")
        except Exception as e:
            logger.error(f"Failed to ingest MQTT manifest: {e}")

    def execute_frame_cycle(self, raw_detections: np.ndarray, timestamp: float = 0.0) -> dict:
        """
        Runs full pipeline within <= 120ms budget:
        raw_detections: [N, 6] (x1, y1, x2, y2, conf, class_id)
        """
        t_start = time.perf_counter()

        if not np.isfinite(timestamp) or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError("Frame timestamps must be finite and strictly increasing")
        dt = 0.1 if self.last_timestamp is None else timestamp - self.last_timestamp
        self.last_timestamp = timestamp
        raw_detections = np.asarray(raw_detections, dtype=np.float32)
        if raw_detections.ndim != 2 or raw_detections.shape[1] != 6 or not np.isfinite(raw_detections).all():
            raise ValueError("Detections must be a finite N by 6 array")
        for track in self.tracks.values():
            track.step_predict(dt=dt)

        # Step 1: Extract anchors & transform to metric space
        bboxes = raw_detections[:, :4] if len(raw_detections) > 0 else np.empty((0, 4))
        anchors_pixel = self.projector.extract_bottom_center_anchors(bboxes)
        metric_positions = self.projector.pixel_to_metric(anchors_pixel)

        if not np.isfinite(metric_positions).all():
            raise ValueError("Camera projection produced nonfinite positions")

        # Step 2: Assign detections globally to predicted tracks. Dictionary
        # order must not swap identities when detection order changes.
        updated_track_ids = []
        track_ids = list(self.tracks)
        assignments = {}
        if len(metric_positions) and track_ids:
            cost = np.full((len(metric_positions), len(track_ids)), 1e9, dtype=np.float64)
            for detection_idx, pos in enumerate(metric_positions):
                for track_idx, tid in enumerate(track_ids):
                    track = self.tracks[tid]
                    distance = np.linalg.norm(track.kf.state[:2] - pos)
                    if track.class_id == int(raw_detections[detection_idx, 5]) and distance < 2.0:
                        cost[detection_idx, track_idx] = distance
            detection_indices, track_indices = linear_sum_assignment(cost)
            assignments = {int(row): track_ids[col] for row, col in zip(detection_indices, track_indices)
                           if cost[row, col] < 2.0}
        for idx, pos in enumerate(metric_positions):
            cls_id = int(raw_detections[idx, 5])
            matched_id = assignments.get(idx)
            if matched_id is not None:
                self.tracks[matched_id].step_update(pos)
                track = self.tracks[matched_id]
                track.history_times.append(timestamp)
                track.history_times = track.history_times[-track.max_history:]
                updated_track_ids.append(matched_id)
            else:
                new_track = MetricTrack(self.next_track_id, cls_id, pos, length=2.0, width=1.0)
                new_track.history_times = [timestamp]
                self.tracks[self.next_track_id] = new_track
                updated_track_ids.append(self.next_track_id)
                self.next_track_id += 1

        # Purge stale tracks
        for tid in list(self.tracks.keys()):
            if tid not in updated_track_ids:
                if self.tracks[tid].time_since_update > 10:
                    del self.tracks[tid]

        # Step 3: Construct Dynamic Graph & Run Forecasting
        active_nodes = [t for t in self.tracks.values() if t.confirmed and len(t.history) >= 5 and t.time_since_update == 0]
        highest_severity = "NORMAL_LEVEL_0"
        evaluated_pairs = []
        forecast_paths = {}

        if self.forecast_available and len(active_nodes) >= 2:
            num_nodes = len(active_nodes)
            node_hist = np.zeros((num_nodes, 30, 8), dtype=np.float32)
            cls_ids = np.zeros(num_nodes, dtype=np.int64)

            for i, node in enumerate(active_nodes):
                hist = np.array(node.history)
                sample_times = timestamp - np.arange(29, -1, -1) * 0.1
                for feature in range(8):
                    node_hist[i, :, feature] = np.interp(sample_times, node.history_times, hist[:, feature])
                cls_ids[i] = node.class_id

            # Assemble proximity edges (radius <= 25m)
            src_edges, dst_edges, edge_feats = [], [], []
            for i in range(num_nodes):
                for j in range(num_nodes):
                    if i != j:
                        dist = np.linalg.norm(node_hist[i, -1, :2] - node_hist[j, -1, :2])
                        if dist <= 25.0:
                            src_edges.append(i)
                            dst_edges.append(j)
                            d_vec = node_hist[j, -1, :2] - node_hist[i, -1, :2]
                            rel_vel = np.linalg.norm(node_hist[j, -1, 2:4] - node_hist[i, -1, 2:4])
                            edge_feats.append([d_vec[0], d_vec[1], dist, rel_vel, 1.0])

            if len(src_edges) > 0:
                t_hist = torch.tensor(node_hist, dtype=torch.float32, device=self.device)
                t_cls = torch.tensor(cls_ids, dtype=torch.long, device=self.device)
                t_edge_idx = torch.tensor([src_edges, dst_edges], dtype=torch.long, device=self.device)
                t_edge_attr = torch.tensor(edge_feats, dtype=torch.float32, device=self.device)

                with torch.no_grad():
                    preds = self.gnn(t_hist, t_cls, t_edge_idx, t_edge_attr)
                    if not all(torch.isfinite(value).all().item() for value in preds.values()):
                        raise RuntimeError("Forecast model returned nonfinite predictions")

                for i, node in enumerate(active_nodes):
                    mode = int(preds['mode_probs'][i].argmax().item())
                    forecast_paths[node.track_id] = torch.stack(
                        [preds['mu_x'][i, mode], preds['mu_y'][i, mode]], dim=-1
                    ).detach().cpu().tolist()

                # Step 4: Evaluate Risk Conflicts
                for i in range(num_nodes):
                    for j in range(i + 1, num_nodes):
                        # Filter for human vs machine (human classes: 0, 1)
                        is_human_a = (active_nodes[i].class_id in [0, 1])
                        is_human_b = (active_nodes[j].class_id in [0, 1])
                        if is_human_a != is_human_b:
                            dict_a = {
                                "footprint_radius": 0.8 if is_human_a else 2.5,
                                "class_name": "WORKER" if is_human_a else "HEAVY_EQUIPMENT",
                                "mode_probs": preds["mode_probs"][i].cpu().numpy(),
                                "mu_x": preds["mu_x"][i].cpu().numpy(),
                                "mu_y": preds["mu_y"][i].cpu().numpy()
                            }
                            dict_b = {
                                "footprint_radius": 0.8 if is_human_b else 2.5,
                                "class_name": "WORKER" if is_human_b else "HEAVY_EQUIPMENT",
                                "mode_probs": preds["mode_probs"][j].cpu().numpy(),
                                "mu_x": preds["mu_x"][j].cpu().numpy(),
                                "mu_y": preds["mu_y"][j].cpu().numpy()
                            }
                            risk = self.conflict_engine.evaluate_pair_risk(dict_a, dict_b)
                            if risk["min_ttc"] >= 0 and (self._last_min_ttc < 0 or risk["min_ttc"] < self._last_min_ttc):
                                self._last_min_ttc = float(risk["min_ttc"])
                                self._last_p_col = float(risk["p_col"])
                            evaluated_pairs.append({
                                "track_a": active_nodes[i].track_id,
                                "track_b": active_nodes[j].track_id,
                                "risk": risk
                            })
                            if risk["state"] == "CRITICAL_LEVEL_3":
                                highest_severity = "CRITICAL_LEVEL_3"
                            elif risk["state"] == "WARNING_LEVEL_2" and highest_severity != "CRITICAL_LEVEL_3":
                                highest_severity = "WARNING_LEVEL_2"
                            elif risk["state"] == "ADVISORY_LEVEL_1" and highest_severity == "NORMAL_LEVEL_0":
                                highest_severity = "ADVISORY_LEVEL_1"

        # Step 5: Debounce & Actuate
        previous_alarm = self.last_alarm_state
        final_alarm = self.debouncer.step(highest_severity)
        self.last_alarm_state = final_alarm
        if final_alarm == "CRITICAL_LEVEL_3":
            self._actuate_physical_relays(True)
        else:
            self._actuate_physical_relays(False)

        # Step 6: Zone occupancy and structured safety events
        self._emit_zone_and_risk_events(active_nodes, evaluated_pairs, previous_alarm, final_alarm)

        dt_ms = (time.perf_counter() - t_start) * 1000.0
        if dt_ms > 120.0:
            logger.warning(f"LATENCY BREACH: Edge execution cycle took {dt_ms:.2f} ms")

        return {
            "forecast_status": "AVAILABLE" if self.forecast_available else "UNAVAILABLE_NO_CHECKPOINT",
            "forecast_paths": forecast_paths,
            "forecast_horizon_seconds": 5.0,
            "hazards": self.conflict_engine.active_manifest_envelopes,
            "cycle_latency_ms": dt_ms,
            "active_tracks_count": len(self.tracks),
            "raw_severity": highest_severity,
            "debounced_alarm": final_alarm,
            "business_state": business_state(final_alarm).value,
            "evaluated_pairs": evaluated_pairs,
            "camera_id": self.camera_id,
            "site_id": self.site_id,
            "site_identity": self.site_identity(),
            "zone_occupancy": self.occupancy.occupancy(),
            "safety_events": [event.model_dump(mode="json") for event in self.event_log.read(limit=12)]
        }

    def load_shift_manifest(self, manifest: dict, *, match: str = "direct") -> int:
        """Load a shift manifest's envelopes and adopt its site identity.

        Returns the number of envelopes loaded. The manifest's ``site_id`` is
        adopted so an event reports the same site as the zone it refers to.
        """
        envelopes = manifest.get("envelopes") or []
        if not envelopes:
            return 0
        self.conflict_engine.load_manifest_envelopes(envelopes)
        self.manifest_site_id = str(manifest.get("site_id") or "")
        self.manifest_shift_id = str(manifest.get("shift_id") or "")
        self.manifest_match = match
        if self.manifest_site_id:
            self.site_id = self.manifest_site_id
        return len(envelopes)

    def site_identity(self) -> dict:
        """The site, shift and source the current zones came from."""
        return {
            "site_id": self.site_id,
            "manifest_site_id": self.manifest_site_id or self.site_id,
            "manifest_shift_id": self.manifest_shift_id,
            "manifest_match": self.manifest_match or "none",
            "zone_source": self.zone_source,
        }

    def zone_overlays(self, frame_width: int, frame_height: int) -> List[Dict[str, Any]]:
        """Project the active zones into camera pixels for the camera panel.

        A zone is only reported when part of it is actually visible in the frame,
        so the panel never draws a zone the camera cannot see. Sites and
        scenario demos are distinguished so the panel can label an illustrative
        zone as such.
        """
        overlays: List[Dict[str, Any]] = []
        for envelope in self.conflict_engine.active_manifest_envelopes:
            coords = envelope.get("polygon") or envelope.get("polygon_metric_epsg3857")
            if not coords:
                continue
            try:
                pixels = self.projector.metric_polygon_to_pixels(
                    np.asarray(coords, dtype=np.float64), frame_width, frame_height
                )
            except (ValueError, np.linalg.LinAlgError):
                continue
            if pixels is None:
                continue
            overlays.append({
                "zone_id": str(envelope.get("envelope_id") or ""),
                "zone_name": str(envelope.get("zone_name") or envelope.get("envelope_id") or ""),
                "machine_id": str(envelope.get("machine_id") or ""),
                "polygon_px": [[round(float(x), 1), round(float(y), 1)] for x, y in pixels],
                "polygon_metric": [[float(x), float(y)] for x, y in coords],
                "severity": str(envelope.get("severity") or ""),
                "zone_source": self.zone_source,
                "calibrated": self.zone_source == "SITE_MANIFEST",
            })
        return overlays

    def _emit_zone_and_risk_events(self, active_nodes, evaluated_pairs, previous_alarm: str, final_alarm: str) -> None:
        """Emit one structured event per occupancy or risk transition.

        Events are produced here, automatically, on the frame that caused them.
        They are not built by a caller and relayed, which is what the project
        cards require (SEC-07.7).
        """
        detection_status = (
            DetectionStatus.ILLUSTRATIVE
            if self.zone_source != "SITE_MANIFEST"
            else DetectionStatus.MEASURED
        )
        machines = {
            str(envelope.get("envelope_id") or ""): str(envelope.get("machine_id") or "")
            for envelope in self.conflict_engine.active_manifest_envelopes
        }

        # Strongest risk each agent is involved in, so an occupancy event can
        # carry the risk that applied while the agent was inside.
        agent_risk: Dict[Any, dict] = {}
        for pair in evaluated_pairs:
            risk = pair.get("risk") or {}
            for key in ("track_a", "track_b"):
                track_id = pair.get(key)
                current = agent_risk.get(track_id)
                if current is None or _level_rank(risk.get("state")) > _level_rank(current.get("state")):
                    agent_risk[track_id] = risk

        agents = []
        for node in active_nodes:
            position = node.kf.state[:2]
            agents.append({
                "agent_id": str(node.track_id),
                "class_name": "WORKER" if node.class_id in (0, 1) else "HEAVY_EQUIPMENT",
                "position": (float(position[0]), float(position[1])),
            })

        transitions = self.occupancy.update(
            self.conflict_engine.active_manifest_envelopes,
            agents,
            risks={key: value for key, value in agent_risk.items()},
        )
        event_types = {
            "ZONE_ENTRY": EventType.ZONE_ENTRY,
            "ZONE_EXIT": EventType.ZONE_EXIT,
            "ZONE_OCCUPANCY": EventType.ZONE_OCCUPANCY,
        }
        for transition in transitions:
            event = build_event(
                event_types[transition.event_type],
                self.camera_id,
                transition.peak_risk_level or "NORMAL_LEVEL_0",
                site_id=self.site_id,
                zone_id=transition.zone_id,
                zone_name=transition.zone_name,
                machine_id=machines.get(transition.zone_id, ""),
                detection_status=detection_status,
                detected_class=transition.agent_class,
                track_id=int(transition.agent_id) if str(transition.agent_id).isdigit() else None,
                dwell_seconds=transition.dwell_seconds,
                peak_risk_level=transition.peak_risk_level,
                min_ttc_seconds=transition.min_ttc_seconds,
                collision_probability=transition.collision_probability,
                forecast_status="AVAILABLE" if self.forecast_available else "UNAVAILABLE_NO_CHECKPOINT",
            )
            self.event_log.append(event)

        # Risk transitions produce their own event so a change of state is never
        # silent, including the return to the safe state.
        if previous_alarm != final_alarm and previous_alarm:
            escalating = LEVEL_ORDER.index(to_risk_level(final_alarm)) > LEVEL_ORDER.index(to_risk_level(previous_alarm))
            # Report the risk from the frame that caused the transition, not a
            # value carried over from an earlier frame.
            frame_ttc, frame_pcol = self._worst_pair_risk(evaluated_pairs)
            worst = self.occupancy.occupancy() or [{}]
            event = build_event(
                EventType.RISK_ESCALATION if escalating else EventType.RISK_DEESCALATION,
                self.camera_id,
                final_alarm,
                site_id=self.site_id,
                zone_id=str(worst[0].get("zone_id") or ""),
                zone_name=str(worst[0].get("zone_name") or ""),
                machine_id=machines.get(str(worst[0].get("zone_id") or ""), ""),
                detection_status=detection_status,
                min_ttc_seconds=frame_ttc,
                collision_probability=frame_pcol,
                forecast_status="AVAILABLE" if self.forecast_available else "UNAVAILABLE_NO_CHECKPOINT",
            )
            self.event_log.append(event)

    @staticmethod
    def _worst_pair_risk(evaluated_pairs) -> tuple:
        """Strongest (time to collision, probability) among this frame's pairs."""
        best_level, best_ttc, best_probability = -1, -1.0, 0.0
        for pair in evaluated_pairs or []:
            risk = pair.get("risk") or {}
            level = _level_rank(risk.get("state"))
            ttc = float(risk.get("min_ttc", -1.0))
            probability = float(risk.get("p_col", 0.0))
            if level > best_level or (level == best_level and probability > best_probability):
                best_level, best_ttc, best_probability = level, ttc, probability
        return best_ttc, best_probability

    def _actuate_physical_relays(self, state: bool):
        # Modbus/TCP command to ADAM-6060 relay coil
        self.relay_triggered = state

    def close(self):
        if self.mqtt_client is not None:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
