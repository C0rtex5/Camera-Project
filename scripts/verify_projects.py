"""Verify the sub-project decomposition of SentinelZone-AI.

This is standalone tooling: it never imports application code and never writes
application data. It reads the tracked files of the repository, the per-project
manifests under ``projects/<id>/project.json`` and ``projects/repo-scope.json``,
and it fails (non-zero exit) on any of the following:

* a tracked file that is not owned by exactly one project and is not declared
  shared or excluded;
* a path owned by two projects (ownership must be exclusive);
* an ownership glob that matches no tracked file (stale claim);
* a test path that does not exist;
* a ``depends_on`` edge that points at an unknown project or creates a cycle;
* a cross-project import edge in ``src/`` that is neither declared in
  ``depends_on`` nor listed in ``coupling_exceptions``;
* a documented coupling exception that no longer exists in the code (the debt
  was paid: the entry must be removed);
* a declared public interface whose file does not exist.

Usage:
    python scripts/verify_projects.py
    python scripts/verify_projects.py --write-report projects/verification-report.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects"
SCOPE = PROJECTS / "repo-scope.json"

REQUIRED_KEYS = (
    "id",
    "name",
    "version",
    "status",
    "purpose",
    "cards",
    "owned_paths",
    "entrypoints",
    "public_interfaces",
    "depends_on",
    "coupling_exceptions",
    "runtime_dependencies",
    "tests",
    "config_env",
    "acceptance_criteria",
    "build",
    "risks",
)
STATUSES = {"planned", "scaffolded", "extraction-ready", "extracted"}


# --------------------------------------------------------------------------- #
# glob + git helpers
# --------------------------------------------------------------------------- #
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a repo-relative glob to a regex.

    ``**`` crosses path separators, ``*`` and ``?`` do not. This is the glob
    dialect documented in projects/INDEX.md and used by every manifest.
    """
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def tracked_files() -> list[str]:
    """Return every repository file git knows about.

    Committed files plus untracked-but-not-ignored files, so a brand-new file is
    subject to the ownership rules before it is committed rather than after.
    """
    try:
        raw = subprocess.run(
            ["git", "ls-files", "-z", "-c", "-o", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        files = [p for p in raw.decode("utf-8", "replace").split("\0") if p]
        if files:
            return sorted(set(files))
    except (OSError, subprocess.CalledProcessError):
        pass
    ignored = {".git", "__pycache__", ".venv", "node_modules"}
    return sorted(
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and not ignored.intersection(path.parts)
    )


# --------------------------------------------------------------------------- #
# import graph
# --------------------------------------------------------------------------- #
def module_to_file(module: str) -> str | None:
    """Map ``src.a.b`` to its repository file, if it exists."""
    rel = module.replace(".", "/")
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        if (ROOT / candidate).is_file():
            return candidate
    return None


def module_name_for(path: str) -> str:
    rel = path[:-3] if path.endswith(".py") else path
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def imported_modules(path: str) -> set[str]:
    """Return the ``src.*`` modules imported by a file (AST only, no import).

    ``from src.api import media`` imports ``src.api.media``, not the package
    ``__init__``; the alias is resolved to its file when one exists and the
    package itself is only counted when no alias resolves.
    """
    try:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            resolved = False
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{node.module}.{alias.name}"
                if module_to_file(candidate):
                    found.add(candidate)
                    resolved = True
            if not resolved:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    found.add(alias.name)
    return found


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: dict[str, object] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load_scope(report: Report) -> dict:
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    for key in ("shared_paths", "excluded_paths", "policy"):
        if key not in scope:
            report.error(f"projects/repo-scope.json: missing '{key}'")
    return scope


def load_projects(report: Report) -> dict[str, dict]:
    projects: dict[str, dict] = {}
    for manifest_path in sorted(PROJECTS.glob("*/project.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.error(f"{manifest_path.relative_to(ROOT)}: invalid JSON ({exc})")
            continue
        rel = str(manifest_path.relative_to(ROOT))
        for key in REQUIRED_KEYS:
            if key not in data:
                report.error(f"{rel}: missing required key '{key}'")
        pid = data.get("id")
        if not pid:
            report.error(f"{rel}: missing id")
            continue
        if pid != manifest_path.parent.name:
            report.error(f"{rel}: id '{pid}' does not match directory name")
        if pid in projects:
            report.error(f"{rel}: duplicate project id '{pid}'")
        if data.get("status") not in STATUSES:
            report.error(f"{rel}: status '{data.get('status')}' not in {sorted(STATUSES)}")
        projects[pid] = data
    if not projects:
        report.error("no projects/*/project.json manifests found")
    return projects


def check_ownership(report: Report, scope: dict, projects: dict[str, dict], tracked: list[str]) -> dict[str, str]:
    """Assign every tracked file to at most one project."""
    compiled: list[tuple[str, str, re.Pattern[str]]] = []
    for pid, data in projects.items():
        for pattern in data.get("owned_paths", []):
            compiled.append((pid, pattern, glob_to_regex(pattern)))

    owners: dict[str, list[str]] = {path: [] for path in tracked}
    matched_glob: dict[tuple[str, str], int] = {(pid, pat): 0 for pid, pat, _ in compiled}
    for path in tracked:
        for pid, pattern, regex in compiled:
            if regex.match(path):
                owners[path].append(pid)
                matched_glob[(pid, pattern)] += 1

    shared = scope.get("shared_paths", [])
    excluded = scope.get("excluded_paths", [])
    shared_re = [glob_to_regex(p) for p in shared]
    excluded_re = [glob_to_regex(p) for p in excluded]

    unowned: list[str] = []
    double: list[str] = []
    for path in tracked:
        found = owners[path]
        is_shared = any(rx.match(path) for rx in shared_re)
        is_excluded = any(rx.match(path) for rx in excluded_re)
        if is_shared and found:
            double.append(f"{path} is shared and owned by {found}")
            continue
        if len(found) > 1:
            double.append(f"{path} owned by {found}")
            continue
        if not found and not is_shared and not is_excluded:
            unowned.append(path)

    for (pid, pattern), count in sorted(matched_glob.items()):
        if count == 0:
            report.error(f"{pid}: owned_paths entry '{pattern}' matches no tracked file")
    for path in unowned:
        report.error(f"unowned tracked file: {path}")
    for path in double:
        report.error(f"ambiguous ownership: {path}")

    assignment = {path: found[0] for path, found in owners.items() if len(found) == 1}
    report.info["tracked_files"] = len(tracked)
    report.info["owned_files"] = len(assignment)
    report.info["shared_files"] = sum(1 for p in tracked if any(rx.match(p) for rx in shared_re))
    return assignment


def check_tests_and_paths(report: Report, projects: dict[str, dict]) -> None:
    for pid, data in projects.items():
        if not (PROJECTS / pid / "README.md").is_file():
            report.error(f"{pid}: missing charter projects/{pid}/README.md")
        tests = data.get("tests", {})
        for kind in ("python", "node"):
            for path in tests.get(kind, []):
                if not (ROOT / path).is_file():
                    report.error(f"{pid}: tests.{kind} entry '{path}' does not exist")
        for path in data.get("entrypoints", []):
            target = path.split(":", 1)[0]
            if not (ROOT / target).is_file():
                report.error(f"{pid}: entrypoint '{path}' does not exist")
        for iface in data.get("public_interfaces", []):
            path = iface.get("path", "")
            if path and not (ROOT / path).exists():
                report.error(f"{pid}: interface '{iface.get('name')}' path '{path}' does not exist")
            for consumer in iface.get("consumers", []):
                if consumer not in projects:
                    report.error(f"{pid}: interface '{iface.get('name')}' names unknown consumer '{consumer}'")


def check_dependencies(report: Report, projects: dict[str, dict]) -> None:
    for pid, data in projects.items():
        for dep in data.get("depends_on", []):
            if dep not in projects:
                report.error(f"{pid}: depends_on unknown project '{dep}'")
        for exc in data.get("coupling_exceptions", []):
            for key in ("from", "to", "reason", "resolution"):
                if not exc.get(key):
                    report.error(f"{pid}: coupling_exception missing '{key}'")
            target = exc.get("to", "")
            owner = next(
                (p for p, d in projects.items() if any(glob_to_regex(g).match(target) for g in d.get("owned_paths", []))),
                None,
            )
            if owner is None:
                report.error(f"{pid}: coupling_exception 'to' path '{target}' is owned by no project")
            elif owner in data.get("depends_on", []):
                report.warn(
                    f"{pid}: coupling_exception for '{target}' is redundant ({owner} is already a declared dependency)"
                )

    # Kahn topological sort over declared dependencies.
    remaining = {pid: set(data.get("depends_on", [])) for pid, data in projects.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(pid for pid, deps in remaining.items() if not deps - set(order))
        if not ready:
            cycle = ", ".join(sorted(remaining))
            report.error(f"declared depends_on cycle among: {cycle}")
            break
        order.append(ready[0])
        remaining.pop(ready[0])
    report.info["build_order"] = order


def check_import_edges(report: Report, projects: dict[str, dict], assignment: dict[str, str]) -> None:
    declared = {pid: set(data.get("depends_on", [])) for pid, data in projects.items()}
    exceptions: dict[str, list[tuple[re.Pattern[str], re.Pattern[str]]]] = {}
    for pid, data in projects.items():
        rows = []
        for exc in data.get("coupling_exceptions", []):
            rows.append((glob_to_regex(exc.get("from", "")), glob_to_regex(exc.get("to", ""))))
        exceptions[pid] = rows

    undeclared: list[str] = []
    used_exceptions: set[tuple[str, str, str]] = set()
    edge_count = 0
    for path, owner in sorted(assignment.items()):
        if owner is None or not path.endswith(".py") or not path.startswith("src/"):
            continue
        for module in sorted(imported_modules(path)):
            target = module_to_file(module)
            if target is None or target not in assignment:
                continue
            other = assignment[target]
            if other == owner:
                continue
            edge_count += 1
            if other in declared.get(owner, set()):
                continue
            matched = False
            for index, (from_rx, to_rx) in enumerate(exceptions.get(owner, [])):
                if from_rx.match(path) and to_rx.match(target):
                    used_exceptions.add((owner, str(index), target))
                    matched = True
                    break
            if not matched:
                undeclared.append(f"{owner}: {path} imports {target} (owned by {other})")

    for line in undeclared:
        report.error(f"undeclared cross-project import: {line}")

    for pid, data in projects.items():
        for index, exc in enumerate(data.get("coupling_exceptions", [])):
            key = (pid, str(index), exc.get("to", ""))
            if key not in used_exceptions:
                report.error(
                    f"{pid}: coupling_exception #{index + 1} ({exc.get('from')} -> {exc.get('to')}) "
                    "no longer matches any import; remove the paid debt"
                )
    report.info["cross_project_import_edges"] = edge_count
    report.info["documented_coupling_exceptions"] = sum(len(v) for v in exceptions.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", metavar="PATH", help="write a JSON verification report")
    parser.add_argument("--quiet", action="store_true", help="only print errors and the summary")
    args = parser.parse_args()

    report = Report()
    scope = load_scope(report)
    projects = load_projects(report)
    if not projects:
        print("\n".join(report.errors))
        return 1
    tracked = tracked_files()
    assignment = check_ownership(report, scope, projects, tracked)
    check_tests_and_paths(report, projects)
    check_dependencies(report, projects)
    check_import_edges(report, projects, assignment)

    by_project: dict[str, int] = {pid: 0 for pid in projects}
    for path, owner in assignment.items():
        by_project[owner] += 1
    report.info["files_per_project"] = dict(sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0])))

    if not args.quiet:
        print("Sub-project decomposition verification")
        print("=" * 60)
        print(f"tracked files        : {report.info['tracked_files']}")
        print(f"owned by one project : {report.info['owned_files']}")
        print(f"shared platform files: {report.info['shared_files']}")
        print(f"projects             : {len(projects)}")
        print(f"cross-project imports: {report.info['cross_project_import_edges']}")
        print(f"documented coupling  : {report.info['documented_coupling_exceptions']}")
        print()
        print("files per project")
        for pid, count in report.info["files_per_project"].items():  # type: ignore[union-attr]
            print(f"  {pid:<24} {count:>4}")
        print()
        print("declared build order")
        print("  " + " -> ".join(report.info["build_order"]))  # type: ignore[arg-type]
        print()
    for warning in report.warnings:
        print(f"WARN  {warning}")
    for error in report.errors:
        print(f"ERROR {error}")

    if args.write_report:
        shared_re = [glob_to_regex(p) for p in scope.get("shared_paths", [])]
        excluded_re = [glob_to_regex(p) for p in scope.get("excluded_paths", [])]
        ownership = {
            path: ("shared" if any(rx.match(path) for rx in shared_re) else assignment.get(path, "excluded"))
            for path in tracked
        }
        payload = {
            "ok": not report.errors,
            "errors": report.errors,
            "warnings": report.warnings,
            "info": report.info,
            "files_per_project": report.info["files_per_project"],
            "ownership": dict(sorted(ownership.items())),
        }
        out = ROOT / args.write_report
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"report written: {out.relative_to(ROOT)}")

    print()
    print(f"RESULT: {'PASS' if not report.errors else 'FAIL'} ({len(report.errors)} errors, {len(report.warnings)} warnings)")
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
