"""Original settings must stay original, and the app must work with them.

The original repository disagrees with itself about the site identity: the
config declares ``SITE-5-EARTHMOVING`` while the active shift manifest is
``SITE-EAST``. The original settings are preserved verbatim and the *code* is
made tolerant, so a live camera actually receives its hazard zones and every
event reports a site that agrees with the zone it refers to.
"""
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.edge.edge_runtime import SentinelEdgeRuntime
from src.edge.manifest_selector import normalise_site, resolve_manifest
from src.schemas.contracts import ShiftSafetyManifest
from src.schemas.safety_event import EventType, build_event

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "bc8e2c88d376575503e77351f1b123b13c547535"


def upstream_blob(path: str) -> bytes:
    result = subprocess.run(["git", "show", f"{UPSTREAM}:{path}"], cwd=ROOT,
                            capture_output=True)
    return result.stdout


class OriginalSettingsPreservedTests(unittest.TestCase):
    """The original repository's settings files must be untouched."""

    SETTINGS = [
        "config/default_config.json",
        "data/manifests/active_manifest.json",
        "data/manifests/SHIFT_2026-09-07_SITE-01.json",
        "data/manifests/SHIFT_2026-09-07_SITE_5.json",
        "data/manifests/SHIFT_2026-09-07_SITE-EAST.json",
        "data/manifests/SHIFT_2026-09-07_SITE-NORTH.json",
        "data/roboflow_downloaded/data.yaml",
    ]

    def test_settings_files_match_the_original_repository(self):
        for path in self.SETTINGS:
            with self.subTest(setting=path):
                self.assertTrue((ROOT / path).is_file(), path)
                current = (ROOT / path).read_bytes()
                self.assertEqual(current, upstream_blob(path),
                                 f"{path} differs from the original repository")

    def test_the_configured_camera_and_site_are_the_original_values(self):
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        self.assertEqual(config["camera_id"], "CAM-02-MAST")
        self.assertEqual(config["site_id"], "SITE-5-EARTHMOVING")

    def test_the_homography_is_the_original_calibration(self):
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        homography = config["homography"]
        self.assertEqual(homography["H"][0], [72.825, 25.245, 640.0])
        self.assertEqual(homography["H"][1], [0.0, -2.612, 760.448])
        self.assertEqual(homography["H"][2], [0.0, 0.0394, 1.0])
        self.assertEqual(homography["K"][0][2], 960.0)
        self.assertEqual(homography["K"][1][2], 540.0)

    def test_the_risk_thresholds_are_the_original_values(self):
        from src.edge.conflict_engine import DynamicConflictEngine
        engine = DynamicConflictEngine()
        self.assertEqual(engine.warning_ttc, 2.5)
        self.assertEqual(engine.critical_ttc, 1.5)
        self.assertEqual(engine.prob_threshold, 0.65)
        self.assertEqual(engine.min_separation_distance, 0.0)

    def test_the_dataset_path_is_portable_rather_than_a_windows_path(self):
        """A deliberate, documented deviation from the original."""
        text = (ROOT / "data" / "construction_safety" / "data.yaml").read_text()
        self.assertIn("path: .", text)
        original = upstream_blob("data/construction_safety/data.yaml").decode()
        self.assertIn("D:/Projects", original, "the original really did carry an absolute path")
        self.assertNotIn("D:/Projects", text)

    def test_the_original_sample_incidents_are_intact(self):
        incidents = sorted((ROOT / "data" / "incidents").glob("INC-*.json"))
        self.assertEqual(len(incidents), 5)


class SiteIdentityTests(unittest.TestCase):
    def test_normalisation_ignores_case_and_separators(self):
        self.assertEqual(normalise_site("SITE-5-EARTHMOVING"), "site5earthmoving")
        self.assertEqual(normalise_site("SITE_5"), "site5")
        self.assertEqual(normalise_site("SITE-EAST"), "siteeast")
        self.assertEqual(normalise_site(""), "")
        self.assertEqual(normalise_site(None), "")

    def test_an_exact_match_wins(self):
        resolved = resolve_manifest("SITE-EAST")
        self.assertEqual(resolved["match"], "exact")
        self.assertEqual(resolved["manifest"]["site_id"], "SITE-EAST")

    def test_a_normalised_match_is_accepted(self):
        resolved = resolve_manifest("SITE_5")
        self.assertIn(resolved["match"], {"exact", "normalised"})
        self.assertEqual(resolved["manifest"]["site_id"], "SITE_5")

    def test_an_unmatched_site_falls_back_to_the_active_manifest(self):
        resolved = resolve_manifest("SITE-5-EARTHMOVING")
        self.assertEqual(resolved["match"], "active_fallback")
        self.assertEqual(resolved["manifest"]["site_id"], "SITE-EAST")
        self.assertTrue(resolved["manifest"]["envelopes"])

    def test_a_live_camera_receives_its_zones(self):
        """The original exact-match check loaded zero envelopes here."""
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        resolved = resolve_manifest(config["site_id"])
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        self.assertTrue(manifest.envelopes, "a live camera must have at least one zone")

    def test_a_missing_manifest_directory_yields_nothing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            empty = Path(folder) / "manifests"
            active = Path(folder) / "active.json"
            self.assertIsNone(resolve_manifest("SITE-5", directory=empty, active=active))


class EventSiteCoherenceTests(unittest.TestCase):
    """An event must not claim a site that contradicts its own zone."""

    def _runtime(self):
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        return SentinelEdgeRuntime(
            homography_cfg=config["homography"], gnn_checkpoint_path=None, mqtt_host=None,
            camera_profile={"camera_id": "CAM-02-MAST",
                            "site_id": "SITE-5-EARTHMOVING", "width": 1920, "height": 1080},
        )

    def test_loading_a_manifest_adopts_its_site(self):
        runtime = self._runtime()
        self.assertEqual(runtime.site_id, "SITE-5-EARTHMOVING")
        resolved = resolve_manifest(runtime.site_id)
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        loaded = runtime.load_shift_manifest(manifest.model_dump(mode="json"), match=resolved["match"])
        self.assertEqual(loaded, len(manifest.envelopes))
        self.assertEqual(runtime.site_id, manifest.site_id)
        self.assertEqual(runtime.manifest_site_id, "SITE-EAST")

    def test_site_identity_is_reported_on_the_packet(self):
        runtime = self._runtime()
        resolved = resolve_manifest(runtime.site_id)
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        runtime.load_shift_manifest(manifest.model_dump(mode="json"), match=resolved["match"])
        identity = runtime.site_identity()
        self.assertEqual(identity["site_id"], "SITE-EAST")
        self.assertEqual(identity["manifest_site_id"], "SITE-EAST")
        self.assertEqual(identity["manifest_shift_id"], manifest.shift_id)
        self.assertEqual(identity["manifest_match"], "active_fallback")

    def test_an_event_reports_the_same_site_as_its_zone(self):
        runtime = self._runtime()
        resolved = resolve_manifest(runtime.site_id)
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        runtime.load_shift_manifest(manifest.model_dump(mode="json"), match=resolved["match"])
        zone = manifest.envelopes[0]
        event = build_event(EventType.ZONE_ENTRY, runtime.camera_id, "ADVISORY_LEVEL_1",
                            site_id=runtime.site_id, zone_id=zone.envelope_id)
        self.assertEqual(event.site_id, manifest.site_id)
        self.assertEqual(event.zone_id, zone.envelope_id)

    def test_zones_actually_produce_events_on_the_live_path(self):
        runtime = self._runtime()
        resolved = resolve_manifest(runtime.site_id)
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        runtime.load_shift_manifest(manifest.model_dump(mode="json"), match=resolved["match"])
        envelope = manifest.envelopes[0].model_dump(mode="json")
        polygon = envelope["polygon_metric_epsg3857"]
        cx = sum(point[0] for point in polygon) / len(polygon)
        cy = sum(point[1] for point in polygon) / len(polygon)
        node = SimpleNamespace(track_id=1, class_id=0,
                               kf=SimpleNamespace(state=[cx, cy, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))
        runtime._emit_zone_and_risk_events([node], [], "NORMAL_LEVEL_0", "NORMAL_LEVEL_0")
        emitted = runtime.event_log.read()
        self.assertTrue(emitted, "a worker inside a manifest zone must raise an event")
        self.assertEqual(emitted[0].event_type.value, "ZONE_ENTRY")
        self.assertEqual(emitted[0].site_id, manifest.site_id)
        self.assertEqual(emitted[0].zone_id, envelope["envelope_id"])

    def test_the_live_service_resolves_a_manifest_instead_of_skipping(self):
        source = (ROOT / "src" / "api" / "live_service.py").read_text()
        self.assertIn("resolve_manifest", source)
        self.assertNotIn("if manifest.site_id == config.site_id:", source,
                         "the exact-match check that left the camera zoneless is still present")


if __name__ == "__main__":
    unittest.main()


class ZoneVisibilityTests(unittest.TestCase):
    """The original manifest zones lie outside the original camera's view.

    All five original shift manifests define their envelope at metric
    x[100,125] y[50,70], which projects to x[2578,3706] through the original
    CAM-02-MAST homography - beyond the right edge of a 1920x1080 frame. The
    projector therefore returns no overlay rather than clamping a zone into a
    place it does not occupy. Metric-space behaviour (occupancy, entry/exit
    events, the 3D twin) is unaffected, because those are ground-plane
    computations. Fixing this needs real site geometry, not a code change.
    """

    def _projector(self):
        import numpy as np
        from src.edge.homography import HomographyProjector
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        homography = config["homography"]
        return HomographyProjector(np.array(homography["K"]), np.array(homography["dist"]),
                                   np.array(homography["H"]))

    def test_every_original_manifest_zone_is_outside_the_original_view(self):
        import numpy as np
        projector = self._projector()
        for path in sorted((ROOT / "data" / "manifests").glob("*.json")):
            manifest = ShiftSafetyManifest.model_validate(json.loads(path.read_text()))
            for envelope in manifest.envelopes:
                with self.subTest(manifest=path.name, zone=envelope.envelope_id):
                    polygon = np.array(envelope.polygon_metric_epsg3857, dtype=float)
                    pixels = projector.metric_to_pixel(polygon)
                    self.assertGreater(pixels[:, 0].max(), 1920,
                                       "this manifest zone is unexpectedly inside the frame")

    def test_an_invisible_zone_yields_no_overlay_rather_than_a_wrong_one(self):
        config = json.loads((ROOT / "config" / "default_config.json").read_text())
        runtime = SentinelEdgeRuntime(
            homography_cfg=config["homography"], gnn_checkpoint_path=None, mqtt_host=None,
            camera_profile={"camera_id": config["camera_id"], "site_id": config["site_id"],
                            "width": 1920, "height": 1080})
        resolved = resolve_manifest(config["site_id"])
        manifest = ShiftSafetyManifest.model_validate(resolved["manifest"])
        self.assertGreater(runtime.load_shift_manifest(manifest.model_dump(mode="json")), 0)
        self.assertEqual(runtime.zone_overlays(1920, 1080), [],
                         "a zone outside the frame must not be drawn on it")

    def test_the_original_calibration_sees_the_ground_near_the_origin(self):
        import numpy as np
        projector = self._projector()
        pixels = projector.metric_to_pixel(np.array([[0.0, 5.0], [-5.0, 10.0]]))
        for x, y in pixels:
            self.assertTrue(0 <= x <= 1920 and 0 <= y <= 1080,
                            "the original calibration should see ground near the origin")
