"""Tests for the duplicate-instance audit (scripts/docker_instance_audit.py).

No Docker daemon is required: a stub ``docker`` executable is placed first on
PATH and answers ``docker ps -a --format {{json .}}`` and ``docker inspect`` from
fixtures. The central fixture reproduces the reported production symptom — one
running Compose container plus a second container from the same image with a
restart policy, both publishing port 8000 — which is what makes them take turns.
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
AUDIT = ROOT / "scripts" / "docker_instance_audit.py"
PRODUCTION = "sentinelzone-ai:production"

STUB = """#!/usr/bin/env python3
import json, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:3] == ["ps", "-a", "--format"]:
    sys.stdout.write((here / "ps.jsonl").read_text())
    sys.exit(0)
if args and args[0] == "inspect":
    wanted = set(args[1:])
    data = json.loads((here / "inspect.json").read_text())
    sys.stdout.write(json.dumps([d for d in data if d["Id"] in wanted]))
    sys.exit(0)
sys.stderr.write("unexpected docker invocation: %r\\n" % (args,))
sys.exit(64)
"""


def container(
    cid: str,
    name: str,
    *,
    running: bool,
    exit_code: int = 0,
    restart: str = "unless-stopped",
    image: str = PRODUCTION,
    project: str | None = None,
    service: str = "sentinel",
    workdir: str | None = None,
    host_port: str | None = "8000",
) -> dict:
    labels = {}
    if project:
        labels = {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
            "com.docker.compose.project.working_dir": workdir or "/home/operator/Camera-Project",
            "com.docker.compose.project.config_files": f"{workdir or '/home/operator/Camera-Project'}/compose.yaml",
        }
    bindings = {} if host_port is None else {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": host_port}]}
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Config": {
            "Image": image,
            "Cmd": ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"],
            "Labels": labels,
        },
        "State": {
            "Status": "running" if running else "exited",
            "Running": running,
            "ExitCode": None if running else exit_code,
        },
        "HostConfig": {"RestartPolicy": {"Name": restart}, "PortBindings": bindings},
    }


def ps_row(entry: dict) -> dict:
    return {
        "ID": entry["Id"],
        "Image": entry["Config"]["Image"],
        "Names": entry["Name"].lstrip("/"),
        "State": entry["State"]["Status"],
        "Status": entry["State"]["Status"],
    }


class AuditTests(unittest.TestCase):
    def run_audit(self, containers: list[dict], *extra: str, path: str | None = None):
        with tempfile.TemporaryDirectory(prefix="sentinel-audit-") as tmp:
            root = Path(tmp)
            (root / "docker").write_text(STUB)
            (root / "docker").chmod((root / "docker").stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            (root / "ps.jsonl").write_text("".join(json.dumps(ps_row(c)) + "\n" for c in containers))
            (root / "inspect.json").write_text(json.dumps(containers))
            env = dict(os.environ)
            env["PATH"] = path if path is not None else f"{root}{os.pathsep}{env.get('PATH', '')}"
            return subprocess.run(
                [sys.executable, str(AUDIT), *extra],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )

    def test_single_running_instance_is_ok(self):
        result = self.run_audit(
            [container("7b069f5eb09f" + "0" * 52, "camera-project-sentinel-1", running=True, project="camera-project")]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT: OK (one instance)", result.stdout)
        self.assertIn("compose project 'camera-project'", result.stdout)

    def test_two_restartable_instances_are_reported_as_taking_turns(self):
        """The reported production symptom: one up, one exited, both publishing 8000."""
        compose_id = "7b069f5eb09f" + "0" * 52
        manual_id = "b52328ac9816" + "0" * 52
        result = self.run_audit(
            [
                container(manual_id, "b52328ac9816", running=False, exit_code=0, restart="always"),
                container(compose_id, "camera-project-sentinel-1", running=True, project="camera-project"),
            ]
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DUPLICATES FOUND", result.stdout)
        self.assertIn("restart policy", result.stdout)
        self.assertIn("they will keep taking turns starting", result.stdout)
        self.assertIn("b52328ac9816", result.stdout)
        self.assertIn("7b069f5eb09f", result.stdout)
        # The remediation must neutralise the restart policy before removal,
        # otherwise Docker starts the duplicate again.
        self.assertIn("docker update --restart=no b52328ac9816", result.stdout)
        self.assertIn("docker rm -f b52328ac9816", result.stdout)
        # The surviving instance must never appear in the removal block.
        removal = result.stdout.split("Keep one instance and remove the rest:")[1]
        self.assertNotIn("docker rm -f 7b069f5eb09f", removal)
        # The duplicate here is not Compose-managed, so the operator is told how
        # to find whatever keeps starting it.
        self.assertIn("plain docker run (no compose labels)", result.stdout)
        self.assertIn("systemctl list-units --type=service --all | grep -i sentinel", result.stdout)

    def test_compose_duplicate_gets_a_compose_down_command(self):
        """When the duplicate is the Compose stack, the fix must tear that stack down."""
        manual_id = "b52328ac9816" + "0" * 52
        compose_id = "7b069f5eb09f" + "0" * 52
        result = self.run_audit(
            [
                container(manual_id, "sentinelzone", running=True, restart="unless-stopped"),
                container(compose_id, "camera-project-sentinel-1", running=False, exit_code=0, project="camera-project"),
            ]
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("docker compose -p camera-project", result.stdout)
        self.assertIn("compose.yaml down", result.stdout)

    def test_contested_port_is_an_error_even_without_two_restart_policies(self):
        result = self.run_audit(
            [
                container("a" * 64, "sentinel-one", running=True, restart="no", project="one"),
                container("b" * 64, "sentinel-two", running=False, exit_code=1, restart="no", project="two"),
            ]
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("host port 0.0.0.0:8000 is claimed by 2 containers", result.stdout)

    def test_json_report_names_the_supervisor_of_each_container(self):
        result = self.run_audit(
            [
                container("a" * 64, "sentinel-one", running=True, project="camera-project"),
                container("b" * 64, "sentinel-two", running=False, restart="always"),
            ],
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        supervisors = {row["id"]: row["supervisor"] for row in payload["containers"]}
        self.assertIn("compose project 'camera-project'", supervisors["a" * 12])
        self.assertIn("plain docker run", supervisors["b" * 12])

    def test_image_filter_ignores_other_sentinelzone_images(self):
        result = self.run_audit(
            [
                container("a" * 64, "sentinel-prod", running=True, project="camera-project"),
                container("b" * 64, "sentinel-media", running=True, image="sentinelzone-ai:demo-media"),
            ],
            "--image",
            PRODUCTION,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("RESULT: OK (one instance)", result.stdout)
        self.assertNotIn("sentinel-media", result.stdout)

    def test_missing_docker_exits_two(self):
        with tempfile.TemporaryDirectory(prefix="sentinel-empty-") as empty:
            result = self.run_audit([], path=empty)
        self.assertEqual(result.returncode, 2)
        self.assertIn("docker is not installed", result.stderr)

    def test_no_containers_reports_how_to_start_one(self):
        result = self.run_audit([])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("No containers from this image exist", result.stdout)
        self.assertIn("docker compose up -d", result.stdout)

    def test_audit_never_executes_a_mutating_docker_command(self):
        """Read-only guarantee, checked on the code that runs, not on the help text.

        The script prints remediation commands for the operator, so the *text*
        contains `docker rm`; what must not exist is a call site. Every call to
        the `docker(...)` helper must be `ps` or `inspect`.
        """
        import ast

        tree = ast.parse(AUDIT.read_text())
        call_sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "docker":
                if node.args and isinstance(node.args[0], ast.List):
                    literal = [e.value for e in node.args[0].elts if isinstance(e, ast.Constant)]
                    call_sites.append(literal)
        self.assertTrue(call_sites, "expected the script to call the docker() helper")
        for literal in call_sites:
            self.assertIn(literal[0], {"ps", "inspect"}, f"unexpected docker subcommand: {literal}")
        # No other subprocess entry point may exist.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}:
                    self.assertIsInstance(node.func.value, ast.Name)
                    self.assertEqual(node.func.value.id, "subprocess")
                    parent_is_helper = any(
                        isinstance(parent, ast.FunctionDef) and parent.name == "docker" for parent in ast.walk(tree)
                    )
                    self.assertTrue(parent_is_helper)


if __name__ == "__main__":
    unittest.main()
