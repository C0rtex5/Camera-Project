"""Audit this host for duplicate SentinelZone containers sharing one job and one port.

Read-only: it never starts, stops, creates or removes anything. It answers three
questions:

1. which containers are running a SentinelZone image;
2. what supervises each one — a Compose project/service (with the checkout
   directory that owns it) or a plain ``docker run``;
3. whether more than one of them publishes the same host port, which is the
   condition that makes two containers take turns owning port 8000 and look like
   one flapping deployment.

When duplicates are found it also runs a read-only scan of enabled systemd units
and the current user's crontab, because ``docker rm -f`` only sticks if nothing
else recreates the container (``--no-host-scan`` disables that scan).

Exit codes:
    0  exactly one supervised instance, no contested port
    1  duplicates found (two containers doing the same job, or one contested port)
    2  docker is unavailable (not installed, or the daemon/permission refused)

Usage:
    python scripts/docker_instance_audit.py
    python scripts/docker_instance_audit.py --image sentinelzone-ai:production
    python scripts/docker_instance_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

RESTART_POLICIES_THAT_RETURN = {"always", "unless-stopped"}


@dataclass
class Instance:
    """One container built from a SentinelZone image."""

    container_id: str
    short_id: str
    name: str
    image: str
    state: str
    running: bool
    exit_code: int | None
    restart_policy: str
    command: list[str]
    labels: dict[str, str] = field(default_factory=dict)
    host_ports: list[str] = field(default_factory=list)

    @property
    def compose_project(self) -> str:
        return self.labels.get("com.docker.compose.project", "")

    @property
    def compose_service(self) -> str:
        return self.labels.get("com.docker.compose.service", "")

    @property
    def compose_dir(self) -> str:
        return self.labels.get("com.docker.compose.project.working_dir", "")

    @property
    def supervisor(self) -> str:
        if self.compose_project:
            where = f" in {self.compose_dir}" if self.compose_dir else ""
            return f"compose project '{self.compose_project}' (service '{self.compose_service}'){where}"
        return "plain docker run (no compose labels)"

    @property
    def returns_by_itself(self) -> bool:
        return self.restart_policy in RESTART_POLICIES_THAT_RETURN


def docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def host_supervisors(pattern: str = "sentinel") -> list[str]:
    """Best-effort, read-only scan for things that could restart a container.

    ``docker rm -f`` only sticks if nothing else recreates the container. This
    scan looks at the two supervisors that are visible without root: enabled
    systemd units and the current user's crontab. It never modifies anything and
    silently reports nothing when the tool is absent or unreadable (for example
    when running unprivileged on a host whose units are visible only to root).
    """
    found: list[str] = []
    probes = (
        (["systemctl", "list-units", "--all", "--type=service", "--no-legend", "--no-pager"], "systemd unit"),
        (["crontab", "-l"], "crontab entry"),
    )
    for argv, label in probes:
        if shutil.which(argv[0]) is None:
            continue
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        for line in (result.stdout or "").splitlines():
            if pattern in line.lower():
                found.append(f"{label}: {line.strip()[:140]}")
    return found


def list_containers(image_filter: str) -> list[Instance]:
    """Return every container whose image matches the filter."""
    listing = docker(["ps", "-a", "--format", "{{json .}}"])
    if listing.returncode != 0:
        raise RuntimeError(listing.stderr.strip() or "docker ps failed")

    rows = []
    for line in listing.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    wanted_repo = image_filter.split(":", 1)[0]
    wanted_tag = image_filter.split(":", 1)[1] if ":" in image_filter else ""
    matched = [
        row
        for row in rows
        if row.get("Image", "").split(":", 1)[0] == wanted_repo
        and (not wanted_tag or row.get("Image", "").split(":", 1)[1] == wanted_tag)
    ]
    if not matched:
        return []

    inspected = docker(["inspect", *[row["ID"] for row in matched]])
    if inspected.returncode != 0:
        raise RuntimeError(inspected.stderr.strip() or "docker inspect failed")
    details = {item["Id"]: item for item in json.loads(inspected.stdout)}

    instances: list[Instance] = []
    for row in matched:
        detail = details.get(row["ID"], {})
        state = detail.get("State", {})
        host_config = detail.get("HostConfig", {}) or {}
        ports: list[str] = []
        for container_port, bindings in (host_config.get("PortBindings") or {}).items():
            for binding in bindings or []:
                host_ip = binding.get("HostIp") or "0.0.0.0"
                host_port = binding.get("HostPort") or ""
                ports.append(f"{host_ip}:{host_port}->{container_port}")
        container_id = detail.get("Id", row["ID"])
        instances.append(
            Instance(
                container_id=container_id,
                short_id=container_id[:12],
                name=(detail.get("Name") or row.get("Names", "")).lstrip("/"),
                image=detail.get("Config", {}).get("Image", row.get("Image", "")),
                state=state.get("Status", row.get("State", "")),
                running=bool(state.get("Running")),
                exit_code=state.get("ExitCode"),
                restart_policy=(host_config.get("RestartPolicy") or {}).get("Name", "") or "no",
                command=list(detail.get("Config", {}).get("Cmd") or []),
                labels=detail.get("Config", {}).get("Labels") or {},
                host_ports=ports,
            )
        )
    return instances


def audit(instances: list[Instance]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the observed instances."""
    errors: list[str] = []
    warnings: list[str] = []

    if not instances:
        return errors, warnings

    restartable = [i for i in instances if i.returns_by_itself]
    if len(restartable) > 1:
        errors.append(
            f"{len(restartable)} containers from this image have a restart policy "
            f"({', '.join(sorted({i.restart_policy for i in restartable}))}): they will keep "
            "taking turns starting and will look like a flapping deployment."
        )

    port_users: dict[str, list[Instance]] = {}
    for instance in instances:
        for published in instance.host_ports:
            port_users.setdefault(published.split("->")[0], []).append(instance)
    for port, users in sorted(port_users.items()):
        if len(users) > 1:
            errors.append(
                f"host port {port} is claimed by {len(users)} containers "
                f"({', '.join(u.short_id for u in users)}); only one can hold it at a time — "
                "that is the alternation."
            )
        elif len(instances) > 1 and users[0].returns_by_itself and not users[0].running:
            warnings.append(
                f"{users[0].short_id} is not running but publishes {port} with restart policy "
                f"'{users[0].restart_policy}': it can take the port the moment the other one stops."
            )

    running = [i for i in instances if i.running]
    if len(running) > 1 and len(restartable) <= 1:
        errors.append(f"{len(running)} containers are running at the same time: {', '.join(i.short_id for i in running)}")

    if len(instances) > 1 and not errors:
        warnings.append(f"{len(instances)} containers exist from this image; only one should be supervised.")

    return errors, warnings


def describe(instance: Instance) -> str:
    where = "running" if instance.running else f"exited ({instance.exit_code})"
    ports = ", ".join(instance.host_ports) or "no published ports"
    return (
        f"  {instance.short_id}  {instance.name}\n"
        f"      image      : {instance.image}\n"
        f"      state      : {where}\n"
        f"      supervisor : {instance.supervisor}\n"
        f"      restart    : {instance.restart_policy}\n"
        f"      ports      : {ports}"
    )


def remediation(instances: list[Instance], keep: Instance | None) -> list[str]:
    lines: list[str] = []
    for instance in instances:
        if keep is not None and instance.container_id == keep.container_id:
            continue
        lines.append(f"  # remove duplicate {instance.short_id} ({instance.name}, {instance.supervisor})")
        lines.append(f"  docker update --restart=no {instance.short_id}   # so it cannot come back")
        lines.append(f"  docker rm -f {instance.short_id}")
        if instance.compose_project:
            config = instance.labels.get("com.docker.compose.project.config_files", "compose.yaml")
            directory = f" --project-directory {instance.compose_dir}" if instance.compose_dir else ""
            lines.append(
                f"  docker compose -p {instance.compose_project}{directory} -f {config} down   # and do not start it again"
            )
        else:
            lines.append(
                "  # this one has no Compose labels: find what starts it (systemd unit, cron, "
                "CI job, or a shell session) and disable that, or it will come back:"
            )
            lines.append("  systemctl list-units --type=service --all | grep -i sentinel")
            lines.append("  crontab -l | grep -i sentinel")
        lines.append("")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="sentinelzone-ai", help="image (or repository) to audit")
    parser.add_argument("--json", action="store_true", help="emit a JSON report instead of text")
    parser.add_argument(
        "--no-host-scan",
        action="store_true",
        help="skip the read-only systemd/crontab scan that looks for whatever recreates a duplicate",
    )
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("docker is not installed on this host", file=sys.stderr)
        return 2
    try:
        instances = list_containers(args.image)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"docker is not usable here: {exc}", file=sys.stderr)
        return 2

    errors, warnings = audit(instances)
    supervisors = [] if (args.no_host_scan or not errors) else host_supervisors()
    keep = min(
        (i for i in instances if i.running),
        key=lambda i: i.short_id,
        default=None,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "image_filter": args.image,
                    "containers": [
                        {
                            "id": i.short_id,
                            "name": i.name,
                            "state": i.state,
                            "running": i.running,
                            "exit_code": i.exit_code,
                            "restart_policy": i.restart_policy,
                            "supervisor": i.supervisor,
                            "compose_project": i.compose_project,
                            "compose_dir": i.compose_dir,
                            "host_ports": i.host_ports,
                        }
                        for i in instances
                    ],
                    "errors": errors,
                    "warnings": warnings,
                    "host_supervisors": supervisors,
                    "ok": not errors,
                },
                indent=2,
            )
        )
        return 0 if not errors else 1

    print(f"SentinelZone instance audit (image filter: {args.image})")
    print("=" * 66)
    if not instances:
        print("No containers from this image exist on this host. Start exactly one with:")
        print("  docker compose up -d")
        return 0
    for instance in instances:
        print(describe(instance))
    print()
    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}")
    if errors:
        print()
        print("Possible supervisors that would recreate a container (read-only scan):")
        if supervisors:
            for line in supervisors:
                print(f"  {line}")
        else:
            print("  none found in systemd units or the current user's crontab")
            print("  (check 'sudo crontab -l', other users' crontabs, and any CI job that runs docker run)")
        print()
        print("Keep one instance and remove the rest:")
        print(f"  # keep: {keep.short_id} ({keep.name})" if keep else "  # keep: none running")
        print("\n".join(remediation(instances, keep)))
    print()
    print(f"RESULT: {'OK (one instance)' if not errors else 'DUPLICATES FOUND'}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
