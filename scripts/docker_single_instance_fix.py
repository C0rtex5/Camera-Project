"""Reduce a host to exactly one SentinelZone container (dry run by default).

Diagnosis lives in ``scripts/docker_instance_audit.py`` (read-only). This script
turns that diagnosis into a plan and, only with ``--apply``, executes it:

1. ``docker update --restart=no <duplicate>`` so a restart policy cannot bring it
   back between removal and the next start;
2. ``docker rm -f <duplicate>``;
3. optionally (``--stop-supervisor``) ``docker compose -p <project> ... down`` for a
   duplicate that is itself a Compose stack, so its supervisor stops recreating it.

The running instance you name with ``--keep`` is never touched. Without
``--apply`` nothing is executed: the exact commands are printed and the exit code
still reports that duplicates exist.

Exit codes:
    0  one instance remains (or the host was already clean)
    1  duplicates exist / the plan did not converge
    2  docker unavailable or the request was invalid

Usage:
    python scripts/docker_single_instance_fix.py                        # show the plan
    python scripts/docker_single_instance_fix.py --apply                # keep the running one
    python scripts/docker_single_instance_fix.py --keep 7b069f5eb09f --apply
    python scripts/docker_single_instance_fix.py --keep 7b069f5eb09f --apply --stop-supervisor
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_instance_audit as audit  # noqa: E402  (single source of truth for the inspection)


def docker(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=180)


def find_keep(instances: list[audit.Instance], wanted: str | None) -> audit.Instance | None:
    if wanted:
        for instance in instances:
            if wanted in {instance.container_id, instance.short_id, instance.name}:
                return instance
        return None
    running = [i for i in instances if i.running]
    if not running:
        return None
    return min(running, key=lambda i: i.short_id)


def build_plan(
    instances: list[audit.Instance],
    keep: audit.Instance | None,
    stop_supervisor: bool,
) -> list[tuple[str, list[str]]]:
    plan: list[tuple[str, list[str]]] = []
    for instance in instances:
        if keep is not None and instance.container_id == keep.container_id:
            continue
        if instance.returns_by_itself:
            plan.append(
                (
                    f"stop {instance.short_id} from coming back ({instance.restart_policy})",
                    ["update", "--restart=no", instance.short_id],
                )
            )
        plan.append((f"remove duplicate {instance.short_id} ({instance.name})", ["rm", "-f", instance.short_id]))
        if stop_supervisor and instance.compose_project:
            config = instance.labels.get("com.docker.compose.project.config_files", "compose.yaml")
            argv = ["compose", "-p", instance.compose_project]
            if instance.compose_dir:
                argv += ["--project-directory", instance.compose_dir]
            argv += ["-f", config, "down"]
            plan.append((f"stop the '{instance.compose_project}' stack from recreating it", argv))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="sentinelzone-ai", help="image (or repository) to audit")
    parser.add_argument("--keep", help="container id, short id or name to keep (default: the running one)")
    parser.add_argument("--apply", action="store_true", help="execute the plan (default: dry run)")
    parser.add_argument(
        "--stop-supervisor",
        action="store_true",
        help="also run 'docker compose ... down' for a duplicate that is a Compose stack",
    )
    parser.add_argument("--settle-seconds", type=float, default=2.0, help="wait before the final re-check")
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("docker is not installed on this host", file=sys.stderr)
        return 2
    try:
        instances = audit.list_containers(args.image)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"docker is not usable here: {exc}", file=sys.stderr)
        return 2

    if not instances:
        print(f"No containers from '{args.image}' exist on this host. Start exactly one with: docker compose up -d")
        return 0

    errors, warnings = audit.audit(instances)
    for warning in warnings:
        print(f"WARN  {warning}")
    if not errors:
        for instance in instances:
            print(f"OK    {instance.short_id}  {instance.name}  ({instance.supervisor})")
        print("RESULT: exactly one instance, nothing to do")
        return 0

    keep = find_keep(instances, args.keep)
    if keep is None:
        print(
            "No keeper could be selected"
            + (f": '{args.keep}' is not one of the containers from this image." if args.keep else " (nothing is running).")
            + " Pass --keep <id> explicitly.",
            file=sys.stderr,
        )
        return 2

    for error in errors:
        print(f"ERROR {error}")
    print()
    print(f"Keep : {keep.short_id}  {keep.name}  ({keep.supervisor})")
    plan = build_plan(instances, keep, args.stop_supervisor)
    print("Plan :")
    for description, argv in plan:
        print(f"  - {description}")
        print(f"      docker {' '.join(argv)}")

    if not args.apply:
        print()
        print("Dry run: nothing was executed. Re-run with --apply to carry out the plan.")
        return 1

    print()
    failures = 0
    for description, argv in plan:
        result = docker(argv)
        status = "ok" if result.returncode == 0 else f"failed ({result.returncode})"
        print(f"  {status:>14}  {description}")
        detail = (result.stderr or result.stdout).strip()
        if result.returncode != 0:
            failures += 1
            if detail:
                print(f"                  {detail.splitlines()[-1][:200]}")
    if failures:
        print("  (a failing 'docker compose down' usually means the Compose CLI plugin is missing; remove that stack by hand)")

    if args.settle_seconds:
        time.sleep(args.settle_seconds)
    print()
    remaining = audit.list_containers(args.image)
    remaining_errors, _ = audit.audit(remaining)
    for instance in remaining:
        state = "running" if instance.running else f"exited ({instance.exit_code})"
        print(f"  {instance.short_id}  {instance.name}  {state}  restart={instance.restart_policy}  ({instance.supervisor})")
    if remaining_errors:
        for error in remaining_errors:
            print(f"ERROR {error}")
        print("RESULT: still not a single instance — a supervisor is recreating the duplicate")
        print("        find it with: systemctl list-units --type=service --all | grep -i sentinel")
        print("                      crontab -l | grep -i sentinel")
        return 1
    print("RESULT: OK (one instance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
