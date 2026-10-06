"""Tests for the original-media and dataset catalog exposed by the application."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import media
from src.api.security import is_public_showcase
from src.perception.dataset_downloader import ROBOFLOW_CLASSES

ROOT = Path(__file__).resolve().parents[1]
REAL_VIDEO_DIR = ROOT / "data" / "real_videos"
TEST_VIDEO_DIR = ROOT / "data" / "test_videos"


class MediaCatalogTests(unittest.TestCase):
    def setUp(self):
        media.catalog(refresh=True)

    def test_media_catalog_lists_original_real_and_test_videos(self):
        items = media.catalog()
        names = {item["filename"] for item in items}
        self.assertIn("Worker_in_excavator_blind_spot.mp4", names)
        self.assertIn("Worker_near_heavy_equipment_exca9.mp4", names)
        self.assertIn("Worker_and_excavator_near_barrier.mp4", names)
        groups = {item["group"] for item in items}
        self.assertEqual(groups, {"real_videos", "test_videos"})

    def test_showcase_videos_are_playable_and_tagged(self):
        showcase = {item["showcase_scenario_id"]: item for item in media.catalog() if item["showcase_scenario_id"]}
        self.assertIn("scenario_worker_in_excavator_blind_spot", showcase)
        self.assertIn("scenario_exca_near_miss", showcase)
        self.assertIn("scenario_worker_and_excavator_near_barrier", showcase)
        for scenario_id, item in showcase.items():
            self.assertTrue(item["playable"], scenario_id)
            self.assertGreater(item["frame_count"], 0, scenario_id)

    def test_media_ids_are_unique(self):
        ids = [item["id"] for item in media.catalog()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_datasets_expose_splits_classes_and_license(self):
        datasets = {entry["id"]: entry for entry in media.datasets()}
        self.assertIn("construction_safety", datasets)
        self.assertIn("roboflow_downloaded", datasets)
        construction = datasets["construction_safety"]
        self.assertEqual(construction["total_images"], 12)
        self.assertEqual(construction["class_count"], 10)
        self.assertTrue(all(split["complete"] for split in construction["splits"]))
        roboflow = datasets["roboflow_downloaded"]
        self.assertEqual(roboflow["total_images"], 307 + 57 + 34)
        # The restored Roboflow export carries its own 17-class taxonomy, which is
        # a superset of the 10-class sample taxonomy used by the local generator.
        self.assertEqual(roboflow["class_count"], 17)
        self.assertGreaterEqual(roboflow["class_count"], len(ROBOFLOW_CLASSES))
        self.assertEqual(roboflow["license"], "CC BY 4.0")
        self.assertIn("roboflow", roboflow["source"])

    def test_resolve_media_path_rewrites_legacy_relative_paths(self):
        resolved = media.resolve_media_path("data/test_videos/crew_site_video.mp4")
        self.assertTrue(Path(resolved).is_file(), resolved)
        self.assertIn("crew_site_video.mp4", resolved)

    def test_summary_reports_counts(self):
        summary = media.summary()
        self.assertGreaterEqual(summary["original_media"], 9)
        self.assertGreaterEqual(summary["playable_media"], 8)
        self.assertEqual(summary["datasets"], 2)
        self.assertGreaterEqual(len(summary["showcase_scenarios"]), 3)


class MediaApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"SENTINEL_MODE": "production"}):
            from src.api.app import app
            cls.client_context = TestClient(app)
            cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_showcase_sources_include_original_media_and_datasets(self):
        payload = self.client.get("/api/v1/showcase/sources").json()
        self.assertGreaterEqual(payload["original_media"]["original_media"], 9)
        self.assertEqual(payload["datasets"], 2)

    def test_media_list_and_detail_endpoints(self):
        payload = self.client.get("/api/v1/showcase/media").json()
        self.assertGreaterEqual(payload["count"], 9)
        media_id = payload["media"][0]["id"]
        detail = self.client.get(f"/api/v1/showcase/media/{media_id}")
        self.assertEqual(detail.status_code, 200)

    def test_showcase_frame_endpoint_returns_jpeg(self):
        payload = self.client.get("/api/v1/showcase/media").json()
        showcase = [item for item in payload["media"] if item["showcase_scenario_id"] == "scenario_worker_in_excavator_blind_spot"][0]
        response = self.client.get(f"/api/v1/showcase/media/{showcase['id']}/frames/0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertTrue(response.content.startswith(b"\xff\xd8"))

    def test_dataset_endpoints_and_manifest(self):
        payload = self.client.get("/api/v1/showcase/datasets").json()
        self.assertEqual(payload["count"], 2)
        ids = {entry["id"] for entry in payload["datasets"]}
        self.assertIn("roboflow_downloaded", ids)
        manifest = self.client.get("/api/v1/showcase/datasets/roboflow_downloaded/manifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn("nc:", manifest.text)

    def test_missing_media_returns_404(self):
        self.assertEqual(self.client.get("/api/v1/showcase/media/does_not_exist").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/showcase/datasets/nope/manifest").status_code, 404)

    def test_new_showcase_paths_are_public(self):
        for path in (
            "/api/v1/showcase/sources",
            "/api/v1/showcase/media",
            "/api/v1/showcase/media/abc",
            "/api/v1/showcase/media/abc/frames/0",
            "/api/v1/showcase/media/abc/file",
            "/api/v1/showcase/datasets",
            "/api/v1/showcase/datasets/abc/manifest",
        ):
            self.assertTrue(is_public_showcase(path), path)
        self.assertFalse(is_public_showcase("/api/v1/showcase/media/abc/secret"))
        self.assertFalse(is_public_showcase("/api/v1/setup/survey"))


if __name__ == "__main__":
    unittest.main()
