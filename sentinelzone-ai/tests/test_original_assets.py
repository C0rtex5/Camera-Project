import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "original_assets_manifest.json"


class OriginalAssetRestorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())

    def test_manifest_pins_upstream_and_showcase_sources(self):
        self.assertEqual(self.manifest["upstream_repository"], "https://github.com/Free-devloper/SentinelZone-AI")
        self.assertEqual(self.manifest["upstream_ref"], "origin/main")
        self.assertEqual(len(self.manifest["upstream_commit"]), 40)
        self.assertEqual(len(self.manifest["showcase_scenarios"]), 3)
        for scenario in self.manifest["showcase_scenarios"]:
            self.assertTrue((ROOT / scenario["source_path"]).is_file(), scenario["source_path"])

    def test_every_recorded_removal_states_a_reason_and_can_be_restored(self):
        """A removal is a recorded decision, never a silent disappearance."""
        removed = self.manifest.get("removed_objects", [])
        for record in removed:
            self.assertTrue(record.get("reason", "").strip(),
                            f"{record['path']} is recorded as removed without a reason")
            self.assertTrue(record.get("removed_on"), record["path"])
            self.assertTrue(record.get("oid"), record["path"])
            self.assertTrue(record["path"] in
                            {item["path"] for item in self.manifest["lfs_objects"]},
                            f"{record['path']} is not an upstream object")
            # Absent, and restorable from the recorded upstream commit.
            self.assertFalse((ROOT / record["path"]).is_file(), record["path"])
            self.assertIn(self.manifest["upstream_commit"], record.get("restore", ""), record["path"])

    def test_removed_objects_are_never_silently_readded_to_the_ui(self):
        """A file above 1080p must not come back through the UI filter's guard."""
        from src.api import media
        for record in self.manifest.get("removed_objects", []):
            for item in media.unique_catalog(refresh=True):
                self.assertNotEqual(item["filename"], Path(record["path"]).name, record["path"])

    def test_lfs_video_objects_match_manifest(self):
        removed = {item["path"] for item in self.manifest.get("removed_objects", [])}
        for item in self.manifest["lfs_objects"]:
            if not item["path"].endswith(".mp4"):
                continue
            if item["path"] in removed:
                continue
            path = ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            self.assertEqual(path.stat().st_size, item["size"], item["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, item["oid"], item["path"])
            self.assertNotEqual(path.read_bytes()[:64], b"version https://git-lfs.github.com/spec/v1")

    def test_showcase_videos_decode_and_have_frames(self):
        for scenario in self.manifest["showcase_scenarios"]:
            path = ROOT / scenario["source_path"]
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertTrue(capture.isOpened(), scenario["source_path"])
                self.assertGreater(int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0), 0, scenario["source_path"])
            finally:
                capture.release()

    def test_dataset_splits_have_image_label_parity(self):
        for name, spec in self.manifest["datasets"].items():
            root = ROOT / spec["path"]
            layout = spec.get("layout", "split_first")
            for split, expected in spec["splits"].items():
                images = sorted(((root / "images" / split) if layout == "images_first" else (root / split / "images")).glob("*"))
                labels = sorted(((root / "labels" / split) if layout == "images_first" else (root / split / "labels")).glob("*"))
                self.assertEqual(len(images), expected, f"{name}/{split} images")
                self.assertEqual(len(labels), expected, f"{name}/{split} labels")
                self.assertEqual({p.stem for p in images}, {p.stem for p in labels}, f"{name}/{split} parity")

    def test_construction_yaml_is_portable(self):
        path = ROOT / "data" / "construction_safety" / "data.yaml"
        data = yaml.safe_load(path.read_text())
        self.assertEqual(str(data["path"]), ".")
        self.assertNotIn("D:/", path.read_text())

    def test_verifier_passes_without_fetching(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_original_assets.py")],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("asset verification passed", result.stdout)
