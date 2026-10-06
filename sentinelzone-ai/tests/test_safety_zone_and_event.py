"""Tests for the hazard zone, structured safety event and 3D state work.

Covers the project-card gaps these changes close: zone entry / continued
presence / exit, the structured event contract, the documented mapping from the
engine's four severity levels to the three business states, and projecting a
zone onto the camera image.
"""
import json
import os
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

import numpy as np

from src.edge.homography import HomographyProjector
from src.edge.zone_occupancy import ZoneOccupancyTracker
from src.schemas.safety_event import (
    BUSINESS_STATE_BY_LEVEL,
    DetectionStatus,
    EventType,
    RiskLevel,
    SafetyEvent,
    SafetyEventLog,
    build_event,
    business_state,
    to_risk_level,
)

ZONE = [{
    "envelope_id": "ENV_TEST_TRENCH",
    "zone_name": "Trench Excavation",
    "machine_id": "MACHINE-EXCAVATOR-07",
    "polygon_metric_epsg3857": [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)],
    "severity": "CRITICAL_EXCLUSION",
    "ttc_multiplier": 1.4,
    "exempt_entities": ["SPOTTER"],
}]


def agent(inside, agent_id="11", klass="WORKER"):
    return {"agent_id": agent_id, "class_name": klass,
            "position": (5.0, 5.0) if inside else (60.0, 60.0)}


class ZoneOccupancyTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ZoneOccupancyTracker(presence_heartbeat_seconds=1.0, track_stale_seconds=3.0)

    def kinds(self, transitions):
        return [t.event_type for t in transitions]

    def test_entry_is_detected_once(self):
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [agent(False)], now=100.0)), [])
        entered = self.tracker.update(ZONE, [agent(True)], now=101.0)
        self.assertEqual(self.kinds(entered), ["ZONE_ENTRY"])
        self.assertTrue(entered[0].inside)
        self.assertEqual(entered[0].zone_id, "ENV_TEST_TRENCH")
        self.assertEqual(entered[0].zone_name, "Trench Excavation")
        # A second frame inside must not re-announce the entry.
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [agent(True)], now=101.2)), [])

    def test_continued_presence_is_observed_on_a_cadence(self):
        self.tracker.update(ZONE, [agent(True)], now=200.0)
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [agent(True)], now=200.5)), [])
        presence = self.tracker.update(ZONE, [agent(True)], now=201.5)
        self.assertEqual(self.kinds(presence), ["ZONE_OCCUPANCY"])
        self.assertAlmostEqual(presence[0].dwell_seconds, 1.5, places=3)
        # Cadence must not depend on the frame rate.
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [agent(True)], now=202.0)), [])

    def test_exit_reports_dwell_time(self):
        self.tracker.update(ZONE, [agent(True)], now=300.0)
        self.tracker.update(ZONE, [agent(True)], now=302.0)
        exited = self.tracker.update(ZONE, [agent(False)], now=307.0)
        self.assertEqual(self.kinds(exited), ["ZONE_EXIT"])
        self.assertAlmostEqual(exited[0].dwell_seconds, 7.0, places=3)
        self.assertEqual(exited[0].reason, "left_polygon")
        self.assertEqual(self.tracker.occupancy(), [])

    def test_lost_track_does_not_latch_occupancy_forever(self):
        self.tracker.update(ZONE, [agent(True)], now=400.0)
        # The agent stops being reported entirely (stream stall or track loss).
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [], now=401.0)), [])
        lost = self.tracker.update(ZONE, [], now=405.0)
        self.assertEqual(self.kinds(lost), ["ZONE_EXIT"])
        self.assertEqual(lost[0].reason, "track_lost")

    def test_multiple_agents_are_tracked_independently(self):
        a, b = agent(True, "11"), agent(True, "12")
        both = self.tracker.update(ZONE, [a, b], now=500.0)
        self.assertEqual(len(both), 2)
        self.assertEqual({t.agent_id for t in both}, {"11", "12"})
        # Agent 12 drops out of this frame but is inside the stale window, so its
        # occupancy is kept: a one-frame detector dropout must not read as an exit.
        self.tracker.update(ZONE, [a], now=501.0)
        self.assertEqual({row["agent_id"] for row in self.tracker.occupancy()}, {"11", "12"})
        # Once the stale window elapses it is closed as lost, not as an exit.
        transitions = self.tracker.update(ZONE, [a], now=504.0)
        lost = [(t.agent_id, t.event_type, t.reason) for t in transitions if t.reason == "track_lost"]
        self.assertEqual(lost, [("12", "ZONE_EXIT", "track_lost")])
        # Agent 11 is still reported, so it keeps heartbeating rather than exiting.
        self.assertIn(("11", "ZONE_OCCUPANCY", "still_inside"),
                      [(t.agent_id, t.event_type, t.reason) for t in transitions])
        self.assertEqual([row["agent_id"] for row in self.tracker.occupancy()], ["11"])

    def test_risk_reached_inside_the_zone_is_remembered(self):
        risk = {"state": "WARNING_LEVEL_2", "min_ttc": 1.2, "p_col": 0.4}
        self.tracker.update(ZONE, [agent(True)], risks={"11": risk}, now=600.0)
        self.tracker.update(ZONE, [agent(True)], risks={"11": {"state": "NORMAL_LEVEL_0", "min_ttc": -1.0, "p_col": 0.0}}, now=601.0)
        exited = self.tracker.update(ZONE, [agent(False)], now=602.0)
        self.assertEqual(exited[0].peak_risk_level, "WARNING_LEVEL_2")
        self.assertAlmostEqual(exited[0].min_ttc_seconds, 1.2, places=3)
        self.assertAlmostEqual(exited[0].collision_probability, 0.4, places=3)

    def test_peaks_do_not_downgrade(self):
        self.tracker.update(ZONE, [agent(True)], risks={"11": {"state": "CRITICAL_LEVEL_3", "min_ttc": 0.4, "p_col": 0.9}}, now=700.0)
        self.tracker.update(ZONE, [agent(True)], risks={"11": {"state": "ADVISORY_LEVEL_1", "min_ttc": 4.0, "p_col": 0.1}}, now=701.0)
        exited = self.tracker.update(ZONE, [agent(False)], now=702.0)
        self.assertEqual(exited[0].peak_risk_level, "CRITICAL_LEVEL_3")

    def test_reset_clears_all_state(self):
        self.tracker.update(ZONE, [agent(True)], now=800.0)
        self.assertTrue(self.tracker.occupancy())
        self.tracker.reset()
        self.assertEqual(self.tracker.occupancy(), [])
        self.assertEqual(self.kinds(self.tracker.update(ZONE, [agent(True)], now=801.0)), ["ZONE_ENTRY"])


class BusinessStateMappingTests(unittest.TestCase):
    def test_four_levels_map_onto_three_business_states(self):
        self.assertEqual(BUSINESS_STATE_BY_LEVEL[RiskLevel.NORMAL].value, "SAFE")
        self.assertEqual(BUSINESS_STATE_BY_LEVEL[RiskLevel.ADVISORY].value, "ATTENTION")
        self.assertEqual(BUSINESS_STATE_BY_LEVEL[RiskLevel.WARNING].value, "RISK")
        self.assertEqual(BUSINESS_STATE_BY_LEVEL[RiskLevel.CRITICAL].value, "RISK")

    def test_mapping_is_total_and_defaults_safely(self):
        for level in ("NORMAL_LEVEL_0", "ADVISORY_LEVEL_1", "WARNING_LEVEL_2", "CRITICAL_LEVEL_3"):
            self.assertIn(business_state(level), {"SAFE", "ATTENTION", "RISK"})
        # An unknown level must not invent a risk.
        self.assertEqual(to_risk_level("nonsense"), RiskLevel.NORMAL)
        self.assertEqual(business_state("nonsense").value, "SAFE")

    def test_only_three_business_states_exist(self):
        self.assertEqual({state.value for state in BUSINESS_STATE_BY_LEVEL.values()}, {"SAFE", "ATTENTION", "RISK"})


class SafetyEventContractTests(unittest.TestCase):
    def test_event_records_every_required_field(self):
        moment = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
        event = build_event(
            EventType.ZONE_ENTRY,
            "CAM-07",
            "CRITICAL_LEVEL_3",
            site_id="SITE-EAST",
            zone_id="ENV_TEST_TRENCH",
            zone_name="Trench Excavation",
            machine_id="MACHINE-EXCAVATOR-07",
            event_timestamp=moment,
            frame_id="frame-1",
            frame_captured_at=moment,
            min_ttc_seconds=0.8,
            collision_probability=0.77,
            detection_status=DetectionStatus.MEASURED,
            detected_class="WORKER",
            track_id=11,
            forecast_status="AVAILABLE",
        )
        self.assertEqual(event.source_camera_id, "CAM-07")
        self.assertEqual(event.zone_id, "ENV_TEST_TRENCH")
        self.assertEqual(event.machine_id, "MACHINE-EXCAVATOR-07")
        self.assertTrue(event.event_timestamp_utc.endswith("+00:00"))
        self.assertTrue(event.frame_captured_at_utc.endswith("+00:00"))
        self.assertEqual(event.risk_level, RiskLevel.CRITICAL)
        self.assertEqual(event.business_state.value, "RISK")
        self.assertEqual(event.event_type, EventType.ZONE_ENTRY)
        self.assertEqual(event.detection_status, DetectionStatus.MEASURED)
        self.assertEqual(event.track_id, 11)
        self.assertTrue(event.advisory_only, "a PoC event must never claim to be a certified control")

    def test_every_event_type_exists(self):
        for name in ("ZONE_ENTRY", "ZONE_EXIT", "ZONE_OCCUPANCY", "RISK_ESCALATION", "RISK_DEESCALATION", "CAMERA_OFFLINE"):
            self.assertTrue(hasattr(EventType, name))

    def test_event_ids_are_unique(self):
        a = build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1")
        b = build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1")
        self.assertNotEqual(a.event_id, b.event_id)

    def test_schema_rejects_an_invalid_probability(self):
        with self.assertRaises(Exception):
            build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1", collision_probability=1.5)

    def test_event_round_trips_through_json(self):
        event = build_event(EventType.ZONE_EXIT, "CAM-1", "WARNING_LEVEL_2", dwell_seconds=3.5)
        restored = SafetyEvent.model_validate(json.loads(event.model_dump_json()))
        self.assertEqual(restored, event)
        self.assertEqual(restored.schema_version, event.schema_version)

    def test_log_writes_one_json_line_per_event(self):
        with TemporaryDirectory() as folder:
            log = SafetyEventLog(directory=folder)
            for index in range(3):
                log.append(build_event(EventType.ZONE_OCCUPANCY, "CAM-1", "ADVISORY_LEVEL_1", dwell_seconds=index))
            files = list(Path(folder).glob("events-*.jsonl"))
            self.assertEqual(len(files), 1)
            lines = [json.loads(line) for line in files[0].read_text().splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual([line["dwell_seconds"] for line in lines], [0.0, 1.0, 2.0])
            self.assertEqual(len(log.read()), 3)

    def test_log_notifies_subscribers_and_survives_a_read_only_sink(self):
        seen = []
        with TemporaryDirectory() as folder:
            log = SafetyEventLog(directory=folder, sink=seen.append)
            log.append(build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1"))
            self.assertEqual(len(seen), 1)

        def broken(_event):
            raise RuntimeError("subscriber exploded")

        with TemporaryDirectory() as folder:
            log = SafetyEventLog(directory=folder, sink=broken)
            log.append(build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1"))
            self.assertEqual(len(log.read()), 1, "a broken subscriber must not lose the event")

    def test_log_survives_an_unwritable_directory(self):
        with TemporaryDirectory() as folder:
            log = SafetyEventLog(directory=os.path.join(folder, "events"), sink=None)
            os.chmod(folder, 0o500)
            try:
                log.append(build_event(EventType.ZONE_ENTRY, "CAM-1", "ADVISORY_LEVEL_1"))
                self.assertEqual(len(log.read()), 1)
            finally:
                os.chmod(folder, 0o700)


class ZoneProjectionTests(unittest.TestCase):
    def setUp(self):
        config = json.loads(Path("config/default_config.json").read_text())["homography"]
        self.projector = HomographyProjector(
            np.array(config["K"]), np.array(config["dist"]), np.array(config["H"]))

    def test_zone_projects_inside_the_frame(self):
        polygon = np.array([(-10.0, 2.0), (12.0, 2.0), (12.0, 25.0), (-10.0, 25.0)])
        pixels = self.projector.metric_polygon_to_pixels(polygon, 1920, 1080)
        self.assertIsNotNone(pixels)
        self.assertGreaterEqual(pixels.shape[0], 3)
        self.assertTrue(((pixels >= 0).all() and (pixels[:, 0] <= 1919).all() and (pixels[:, 1] <= 1079).all()))

    def test_zone_outside_the_field_of_view_is_not_reported(self):
        far = np.array([(5000.0, 5000.0), (5100.0, 5000.0), (5100.0, 5100.0), (5000.0, 5100.0)])
        self.assertIsNone(self.projector.metric_polygon_to_pixels(far, 1920, 1080))

    def test_degenerate_polygons_are_rejected(self):
        self.assertIsNone(self.projector.metric_polygon_to_pixels(np.zeros((0, 2)), 1920, 1080))
        self.assertIsNone(self.projector.metric_polygon_to_pixels(np.array([[0.0, 0.0], [1.0, 1.0]]), 1920, 1080))
        # A polygon that is a single repeated point has no area to draw.
        self.assertIsNone(self.projector.metric_polygon_to_pixels(np.array([[1.0, 1.0]] * 4), 1920, 1080))

    def test_projection_round_trips_a_point_inside_the_zone(self):
        polygon = np.array([(-10.0, 2.0), (12.0, 2.0), (12.0, 25.0), (-10.0, 25.0)])
        pixels = self.projector.metric_polygon_to_pixels(polygon, 1920, 1080)
        for probe in [(0.0, 6.0), (10.0, 20.0), (-8.0, 23.0)]:
            point = np.array([probe])
            back = self.projector.pixel_to_metric(self.projector.metric_to_pixel(point))
            self.assertLess(abs(back[0][0] - probe[0]), 1.5)
            self.assertLess(abs(back[0][1] - probe[1]), 1.5)

    def test_projected_outline_contains_its_own_interior(self):
        import cv2
        polygon = np.array([(-10.0, 2.0), (12.0, 2.0), (12.0, 25.0), (-10.0, 25.0)])
        pixels = self.projector.metric_polygon_to_pixels(polygon, 1920, 1080)
        centre = self.projector.metric_to_pixel(np.array([[1.0, 12.0]]))[0]
        self.assertGreater(cv2.pointPolygonTest(pixels.astype(np.float32), (float(centre[0]), float(centre[1])), False), 0)


class PacketZoneRemovalTests(unittest.TestCase):
    """The zone overlay and the fabricated machine identity were removed.

    The only zone polygon left in the application is the original repository's
    own BIM trench rectangle, which the 3D canvas draws from the original code.
    """

    CLIP = "real_videos_Worker_in_excavator_blind_spot"

    def setUp(self):
        import os
        os.environ["SENTINEL_MEDIA_PREWARM"] = "0"
        from src.api import media_tracks
        media_tracks.clear_trackers()

    def test_the_packet_no_longer_carries_a_zone_overlay_or_machine(self):
        from src.api import hub
        packet = hub.original_frame(self.CLIP, 120)
        for removed in ("zone_overlays", "machine_id", "zone_id"):
            self.assertNotIn(removed, packet, f"{removed} must no longer be sent")

    def test_the_only_zone_is_the_original_trench_rectangle(self):
        from src.api import hub
        from src.api.media_tracks import CANONICAL_ZONE
        packet = hub.original_frame(self.CLIP, 120)
        self.assertEqual(packet["zone"], CANONICAL_ZONE)
        self.assertEqual(packet["zone"]["zone_name"], "BIM Trench Hazard")

    def test_the_camera_panel_keeps_its_timestamp_and_state(self):
        from src.api import hub
        packet = hub.original_frame(self.CLIP, 120)
        self.assertIn("captured_at", packet)
        self.assertIn(packet["business_state"], {"SAFE", "ATTENTION", "RISK"})


class _FakeTrack:
    """Minimal stand-in for a MetricTrack, as the emitter consumes it."""

    def __init__(self, track_id, class_id, x, y):
        self.track_id = track_id
        self.class_id = class_id
        self.kf = SimpleNamespace(state=[x, y, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])


def _runtime(tmpdir):
    from src.edge.edge_runtime import SentinelEdgeRuntime
    config = json.loads(Path("config/default_config.json").read_text())["homography"]
    return SentinelEdgeRuntime(
        homography_cfg=config,
        gnn_checkpoint_path=None,
        mqtt_host=None,
        camera_profile={"camera_id": "CAM-07", "site_id": "SITE-EAST", "width": 1920, "height": 1080},
    )


class AutomaticEventEmissionTests(unittest.TestCase):
    """SEC-07.7: events must be produced by the pipeline, not by a caller."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = _runtime(self.tmp.name)
        self.runtime.event_log.directory = Path(self.tmp.name) / "events"
        self.runtime.conflict_engine.load_manifest_envelopes(ZONE)

    def kinds(self):
        return [event.event_type.value for event in self.runtime.event_log.read()]

    def test_a_worker_entering_the_zone_produces_an_event_automatically(self):
        inside = [_FakeTrack(11, 0, 5.0, 5.0)]
        self.runtime._emit_zone_and_risk_events(inside, [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.assertEqual(self.kinds(), ["ZONE_ENTRY"])
        event = self.runtime.event_log.read()[-1]
        self.assertEqual(event.source_camera_id, "CAM-07")
        self.assertEqual(event.site_id, "SITE-EAST")
        self.assertEqual(event.zone_id, "ENV_TEST_TRENCH")
        self.assertEqual(event.machine_id, "MACHINE-EXCAVATOR-07")
        self.assertEqual(event.detected_class, "WORKER")
        self.assertEqual(event.track_id, 11)
        self.assertEqual(event.business_state.value, "SAFE")
        self.assertTrue(event.advisory_only)

    def test_presence_and_exit_are_emitted_as_the_worker_moves(self):
        inside = [_FakeTrack(11, 0, 5.0, 5.0)]
        outside = [_FakeTrack(11, 0, 60.0, 60.0)]
        self.runtime._emit_zone_and_risk_events(inside, [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        time.sleep(1.05)
        self.runtime._emit_zone_and_risk_events(inside, [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.runtime._emit_zone_and_risk_events(outside, [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.assertEqual(self.kinds(), ["ZONE_ENTRY", "ZONE_OCCUPANCY", "ZONE_EXIT"])
        exit_event = self.runtime.event_log.read()[-1]
        self.assertGreaterEqual(exit_event.dwell_seconds, 1.0)

    def test_a_risk_escalation_produces_its_own_event(self):
        pairs = [{"track_a": 11, "track_b": 12, "risk": {"state": "CRITICAL_LEVEL_3", "min_ttc": 0.4, "p_col": 0.9}}]
        self.runtime._emit_zone_and_risk_events([], pairs, "NORMAL_LEVEL_0", "CRITICAL_LEVEL_3")
        self.assertIn("RISK_ESCALATION", self.kinds())
        event = [e for e in self.runtime.event_log.read() if e.event_type.value == "RISK_ESCALATION"][0]
        self.assertEqual(event.business_state.value, "RISK")
        self.assertEqual(event.risk_level.value, "CRITICAL_LEVEL_3")
        self.assertAlmostEqual(event.collision_probability, 0.9, places=3)

    def test_a_return_to_safe_produces_a_deescalation(self):
        self.runtime.last_alarm_state = "WARNING_LEVEL_2"
        self.runtime._emit_zone_and_risk_events([], [], "WARNING_LEVEL_2", "NORMAL_LEVEL_0")
        self.assertIn("RISK_DEESCALATION", self.kinds())
        event = self.runtime.event_log.read()[-1]
        self.assertEqual(event.business_state.value, "SAFE")

    def test_no_event_when_nothing_changes(self):
        self.runtime._emit_zone_and_risk_events([], [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.assertEqual(self.kinds(), [])

    def test_events_are_written_to_the_sink(self):
        self.runtime._emit_zone_and_risk_events([_FakeTrack(11, 0, 5.0, 5.0)], [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        files = list((Path(self.tmp.name) / "events").glob("events-*.jsonl"))
        self.assertEqual(len(files), 1)
        lines = [json.loads(line) for line in files[0].read_text().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event_type"], "ZONE_ENTRY")
        self.assertEqual(lines[0]["schema_version"], "sentinel.safety_event/1.0")

    def test_a_site_manifest_produces_measured_events(self):
        self.runtime.zone_source = "SITE_MANIFEST"
        self.runtime._emit_zone_and_risk_events([_FakeTrack(11, 0, 5.0, 5.0)], [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.assertEqual(self.runtime.event_log.read()[-1].detection_status.value, "MEASURED")

    def test_a_scenario_configuration_produces_illustrative_events(self):
        self.runtime.zone_source = "SCENARIO_CONFIG"
        self.runtime._emit_zone_and_risk_events([_FakeTrack(11, 0, 5.0, 5.0)], [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        self.assertEqual(self.runtime.event_log.read()[-1].detection_status.value, "ILLUSTRATIVE")


class LiveZoneOverlayTests(unittest.TestCase):
    """A live camera projects its manifest zones into the frame."""

    def test_manifest_zone_is_projected_and_marked_calibrated(self):
        runtime = _runtime(None)
        runtime.zone_source = "SITE_MANIFEST"
        runtime.conflict_engine.load_manifest_envelopes([{
            "envelope_id": "ENV_LIVE", "zone_name": "Live Trench", "machine_id": "MACHINE-07",
            "polygon_metric_epsg3857": [(-10.0, 2.0), (12.0, 2.0), (12.0, 25.0), (-10.0, 25.0)],
        }])
        overlays = runtime.zone_overlays(1920, 1080)
        self.assertEqual(len(overlays), 1)
        self.assertTrue(overlays[0]["calibrated"])
        self.assertEqual(overlays[0]["zone_source"], "SITE_MANIFEST")
        self.assertEqual(overlays[0]["machine_id"], "MACHINE-07")
        self.assertGreaterEqual(len(overlays[0]["polygon_px"]), 3)

    def test_no_envelopes_means_no_overlay(self):
        runtime = _runtime(None)
        self.assertEqual(runtime.zone_overlays(1920, 1080), [])
