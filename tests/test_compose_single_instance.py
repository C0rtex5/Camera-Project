"""Compose invariants that keep one container per host.

Two containers from the same image that both publish port 8000 and both carry a
restart policy take turns owning the port: whichever loses the race exits, its
restart policy brings it back, and the deployment looks like it is flapping. These
tests pin the file-level properties that prevent the repository from recreating
that situation, and they run without a Docker daemon.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "compose.yaml"
OVERLAYS = ["compose.gpu.yaml", "compose.ssh.yaml", "compose.demo.yaml"]
PINNED_PROJECT = "sentinelzone"
PINNED_CONTAINER = "sentinelzone"


def load(name: str) -> dict:
    with (ROOT / name).open() as handle:
        return yaml.safe_load(handle)


class ComposeSingleInstanceTests(unittest.TestCase):
    def test_base_pins_the_project_and_container_identity(self):
        base = load("compose.yaml")
        self.assertEqual(
            base.get("name"),
            PINNED_PROJECT,
            "the Compose project name must be pinned, otherwise a second checkout "
            "creates a second stack with its own container and volume",
        )
        sentinel = base["services"]["sentinel"]
        self.assertEqual(sentinel.get("container_name"), PINNED_CONTAINER)

    def test_base_service_restarts_and_publishes_exactly_one_host_port(self):
        sentinel = load("compose.yaml")["services"]["sentinel"]
        self.assertIn(sentinel.get("restart"), {"unless-stopped", "always"})
        self.assertEqual(sentinel.get("ports"), ["0.0.0.0:8000:8000"])

    def test_no_other_compose_file_publishes_the_same_host_port(self):
        for name in OVERLAYS:
            overlay = load(name)
            for service_name, service in (overlay.get("services") or {}).items():
                self.assertNotIn(
                    "ports",
                    service or {},
                    f"{name}:{service_name} publishes host ports; only the base stack may own port 8000",
                )

    def test_data_volume_has_a_pinned_name(self):
        volumes = load("compose.yaml")["volumes"]
        self.assertIn("sentinel-data", volumes)
        self.assertEqual(
            (volumes["sentinel-data"] or {}).get("name"),
            "sentinelzone-state",
            "an explicit volume name keeps the persisted camera setup, surveys and manifests "
            "stable when the checkout directory is renamed",
        )

    def test_overlays_do_not_change_the_identity_or_target_another_service(self):
        for name in OVERLAYS:
            overlay = load(name)
            self.assertNotIn("name", overlay, f"{name} must not redefine the project name")
            self.assertNotIn("volumes", {k: v for k, v in overlay.items() if k == "volumes"}, f"{name}: unexpected top-level volumes")
            for service_name, service in (overlay.get("services") or {}).items():
                self.assertEqual(service_name, "sentinel", f"{name} targets an unknown service")
                self.assertNotIn("container_name", service or {}, f"{name} must not rename the container")

    def test_base_service_is_buildable_or_pulled(self):
        sentinel = load("compose.yaml")["services"]["sentinel"]
        self.assertTrue(sentinel.get("image") or sentinel.get("build"))

    def test_overlays_only_extend_services_defined_in_the_base(self):
        """Overlays are merged with the base, so they may omit image/build — but they
        must not introduce a second service that the base does not define."""
        base_services = set(load("compose.yaml")["services"])
        for name in OVERLAYS:
            for service_name in (load(name).get("services") or {}):
                self.assertIn(service_name, base_services, f"{name} defines an unknown service")


if __name__ == "__main__":
    unittest.main()
