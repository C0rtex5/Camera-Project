"""Tests for scripts/docker_single_instance_fix.py.

The stub ``docker`` keeps persistent state (``state.json``) and records every
invocation (``calls.txt``), so the tests can assert three things no inspection of
the source could: that a dry run executes nothing, that the removal sequence is
``update --restart=no`` *before* ``rm -f``, and that the container named with
``--keep`` is never touched.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "scripts" / "docker_single_instance_fix.py"

sys.path.insert(0, str(ROOT / "tests"))
from test_docker_instance_audit import container  # noqa: E402  (shared fixtures)

STUB = '''#!/usr/bin/env python3
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "calls.txt").open("a") as fh:
    fh.write(" ".join(args) + "\\n")
state_path = here / "state.json"
state = json.loads(state_path.read_text())

def save():
    state_path.write_text(json.dumps(state))

if args[:3] == ["ps", "-a", "--format"]:
    rows = [
        {
            "ID": c["Id"],
            "Image": c["Config"]["Image"],
            "Names": c["Name"].lstrip("/"),
            "State": c["State"]["Status"],
            "Status": c["State"]["Status"],
        }
        for c in state
    ]
    sys.stdout.write("".join(json.dumps(r) + "\\n" for r in rows))
    sys.exit(0)

if args and args[0] == "inspect":
    wanted = set(args[1:])
    sys.stdout.write(json.dumps([c for c in state if c["Id"] in wanted]))
    sys.exit(0)

if args[:2] == ["update", "--restart=no"]:
    target = args[2]
    for c in state:
        if c["Id"] == target or c["Id"].startswith(target):
            c["HostConfig"]["RestartPolicy"]["Name"] = "no"
    save()
    print("updated " + target)
    sys.exit(0)

if args[:2] == ["rm", "-f"]:
    target = args[2]
    state[:] = [c for c in state if c["Id"] != target and not c["Id"].startswith(target)]
    save()
    print("removed " + target)
    sys.exit(0)

if args and args[0] == "compose":
    print("stack down")
    sys.exit(0)

sys.stderr.write("unexpected docker invocation: %r\\n" % (args,))
sys.exit(64)
'''


class FixTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sentinel-fix-")
        self.root = Path(self._tmp.name)
        (self.root / "docker").write_text(STUB)
        (self.root / "docker").chmod(0o755)
        (self.root / "calls.txt").write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    def write_state(self, containers: list[dict]):
        (self.root / "state.json").write_text(json.dumps(containers))

    def calls(self) -> list[str]:
        return [line for line in (self.root / "calls.txt").read_text().splitlines() if line.strip()]

    def run_fix(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PATH=f"{self.root}{os.pathsep}{os.environ.get('PATH', '')}")
        return subprocess.run(
            [sys.executable, str(FIX), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
        )

    @staticmethod
    def flapping_pair() -> list[dict]:
        """The reported production symptom: compose container up, second one exited."""
        return [
            container("b52328ac9816" + "0" * 52, "7b069f5eb09f", running=False, exit_code=0, restart="always"),
            container(
                "7b069f5eb09f" + "0" * 52,
                "camera-project-sentinel-1",
                running=True,
                project="camera-project",
                workdir="/home/bhay_m/Camera-Project",
            ),
        ]

    def test_clean_host_reports_nothing_to_do(self):
        self.write_state([container("a" * 64, "sentinelzone", running=True, project="sentinelzone")])
        result = self.run_fix()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nothing to do", result.stdout)
        self.assertNotIn("update", " ".join(self.calls()))

    def test_no_containers_points_at_one_start_command(self):
        self.write_state([])
        result = self.run_fix()
        self.assertEqual(result.returncode, 0)
        self.assertIn("docker compose up -d", result.stdout)

    def test_dry_run_plans_without_executing_anything(self):
        self.write_state(self.flapping_pair())
        result = self.run_fix()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Dry run", result.stdout)
        self.assertIn("docker update --restart=no b52328ac9816", result.stdout)
        self.assertIn("docker rm -f b52328ac9816", result.stdout)
        # Only inspection happened.
        for call in self.calls():
            self.assertTrue(call.startswith("ps ") or call.startswith("inspect "), call)
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(len(state), 2, "dry run must not change host state")

    def test_apply_keeps_the_named_container_and_removes_the_duplicate(self):
        self.write_state(self.flapping_pair())
        result = self.run_fix("--keep", "camera-project-sentinel-1", "--apply", "--settle-seconds", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT: OK (one instance)", result.stdout)
        calls = self.calls()
        update_at = next(i for i, c in enumerate(calls) if c.startswith("update --restart=no b52328ac9816"))
        remove_at = next(i for i, c in enumerate(calls) if c.startswith("rm -f b52328ac9816"))
        self.assertLess(update_at, remove_at, "restart policy must be cleared before removal")
        for call in calls:
            if call.startswith(("update ", "rm ", "compose ")):
                self.assertNotIn("7b069f5eb09f", call, f"the keeper must never be mutated: {call}")
        remaining = json.loads((self.root / "state.json").read_text())
        self.assertEqual([c["Name"] for c in remaining], ["/camera-project-sentinel-1"])

    def test_keep_defaults_to_the_running_container(self):
        self.write_state(self.flapping_pair())
        result = self.run_fix("--apply", "--settle-seconds", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Keep : 7b069f5eb09f", result.stdout)
        remaining = json.loads((self.root / "state.json").read_text())
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0]["State"]["Running"])

    def test_unknown_keep_is_rejected_without_touching_anything(self):
        self.write_state(self.flapping_pair())
        result = self.run_fix("--keep", "does-not-exist", "--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("is not one of the containers", result.stderr)
        self.assertEqual(json.loads((self.root / "state.json").read_text()).__len__(), 2)

    def test_stop_supervisor_tears_down_the_duplicate_stack(self):
        duplicate = container("b" * 64, "other-sentinel-1", running=False, exit_code=0, project="other-checkout")
        keeper = container("a" * 64, "sentinelzone", running=True, project="sentinelzone")
        self.write_state([keeper, duplicate])
        result = self.run_fix("--keep", "a" * 12, "--apply", "--stop-supervisor", "--settle-seconds", "0")
        calls = self.calls()
        self.assertTrue(any(c.startswith("compose -p other-checkout") and c.endswith("down") for c in calls), calls)
        self.assertIn("RESULT: OK (one instance)", result.stdout)

    def test_duplicate_that_returns_is_reported_as_still_split(self):
        """If a supervisor recreates the container, the run must not claim success."""
        duplicate = container("b" * 64, "dup", running=False, exit_code=0, restart="always")
        keeper = container("a" * 64, "sentinelzone", running=True, project="sentinelzone")
        self.write_state([keeper, duplicate])
        # A stub that refuses to forget the duplicate models a supervisor recreating it.
        (self.root / "docker").write_text(STUB.replace('state[:] = [c for c in state if c["Id"] != target and not c["Id"].startswith(target)]', "pass"))
        result = self.run_fix("--apply", "--settle-seconds", "0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("still not a single instance", result.stdout)


if __name__ == "__main__":
    unittest.main()
