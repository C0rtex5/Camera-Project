"""Run one sub-project's owned test slice.

The slice is read from ``projects/<id>/project.json`` so the manifest stays the
single source of truth for ownership. Python slices run under an isolated
temporary data root (the same protection ``scripts/run_tests.py`` uses) and Node
slices run with ``node --test``. Tooling only: no application code is imported
during setup and no repository record is modified.

Usage:
    python scripts/run_project_tests.py dashboard-ui
    python scripts/run_project_tests.py --list
    python scripts/run_project_tests.py all --keep-going
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects"


def project_ids() -> list[str]:
    return sorted(p.parent.name for p in PROJECTS.glob("*/project.json"))


def load(pid: str) -> dict:
    return json.loads((PROJECTS / pid / "project.json").read_text(encoding="utf-8"))


def isolated_root() -> tempfile.TemporaryDirectory:
    """Temporary cwd with repository inputs symlinked and data/ isolated."""
    tmp = tempfile.TemporaryDirectory(prefix=f"sentinel-{os.getpid()}-")
    root = Path(tmp.name)
    for name in ("src", "config", "weights", "yolov8n.pt"):
        target = ROOT / name
        if target.exists():
            (root / name).symlink_to(target, target_is_directory=target.is_dir())
    (root / "data").mkdir()
    if (ROOT / "data").is_dir():
        for child in (ROOT / "data").iterdir():
            if child.name in {"incidents", "manifests", "active_learning", "adjudications"}:
                continue
            (root / "data" / child.name).symlink_to(child, target_is_directory=child.is_dir())
    os.environ["YOLO_CONFIG_DIR"] = str(root / "yolo")
    os.environ["MPLCONFIGDIR"] = str(root / "matplotlib")
    return tmp


def run_python_slice(pid: str, tests: list[str]) -> tuple[int, str]:
    if not tests:
        return 0, "no python tests owned"
    names = [t[:-3].replace("/", ".") for t in tests if t.endswith(".py") and not t.endswith("__init__.py")]
    if not names:
        return 0, "no python test modules owned"
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("SENTINEL_MODE", "demo")
    os.environ.pop("SENTINEL_API_TOKEN", None)
    tmp = isolated_root()
    previous = Path.cwd()
    try:
        os.chdir(tmp.name)
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        stream = sys.stdout
        result = unittest.TextTestRunner(stream=stream, verbosity=1, buffer=False).run(suite)
        summary = (
            f"run={result.testsRun} failures={len(result.failures)} "
            f"errors={len(result.errors)} skipped={len(result.skipped)}"
        )
        return (0 if result.wasSuccessful() else 1), summary
    finally:
        os.chdir(previous)
        tmp.cleanup()


def run_node_slice(tests: list[str]) -> tuple[int, str]:
    if not tests:
        return 0, "no node tests owned"
    try:
        proc = subprocess.run(
            ["node", "--test", *tests],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return 1, "node not installed"
    tail = [line for line in proc.stdout.splitlines() if line.startswith("ℹ")]
    return proc.returncode, " | ".join(tail) if tail else f"exit={proc.returncode}"


def run(pid: str) -> tuple[bool, str]:
    data = load(pid)
    tests = data.get("tests", {})
    python_rc, python_summary = run_python_slice(pid, tests.get("python", []))
    node_rc, node_summary = run_node_slice(tests.get("node", []))
    ok = python_rc == 0 and node_rc == 0
    detail = f"python: {python_summary}; node: {node_summary}"
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", help="sub-project id, or 'all'")
    parser.add_argument("--list", action="store_true", help="list sub-project ids")
    parser.add_argument("--keep-going", action="store_true", help="continue after a failing project")
    args = parser.parse_args()

    ids = project_ids()
    if args.list or not args.project:
        for pid in ids:
            print(pid)
        return 0

    targets = ids if args.project == "all" else [args.project]
    for pid in targets:
        if pid not in ids:
            print(f"unknown sub-project '{pid}' (see --list)")
            return 2

    failures = 0
    for pid in targets:
        print(f"=== {pid} ===")
        ok, detail = run(pid)
        print(f"  {'PASS' if ok else 'FAIL'} - {detail}")
        if not ok:
            failures += 1
            if not args.keep_going and len(targets) > 1:
                print("  stopping (use --keep-going to run the rest)")
                break
    print(f"\nRESULT: {len(targets) - failures}/{len(targets)} sub-project slices passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
