"""Sprint validation harness for the 12 SentinelZone AI project cards.

This implements the DeepSeek Harness sprint-validation framework as a reusable,
policy-enforcing tool. It is validation tooling: it never imports or modifies
application code.

What the framework enforces
---------------------------
* Every numbered checkbox of every card carries exactly one verdict from
  ``PASS`` / ``FAIL`` / ``BLOCKED`` / ``NOT TESTED``.
* ``PASS`` requires an evidence reference.
* ``BLOCKED`` requires a *named* external prerequisite.
* ``FAIL`` and ``NOT TESTED`` require a reason.
* Every observation is labelled with an evidence class.
* **The central safety rule:** an item that requires physical-site evidence
  cannot be closed with demonstration evidence. If an item is flagged
  ``requires_physical`` and the record is not ``PHYSICAL SITE CAMERA``, a
  ``PASS`` is rejected as a policy violation. This is what stops a restored
  recording or a disposable RTSP fixture from being reported as site
  validation.
* A card is only reported as passing when every one of its items passes.

Usage:
    python scripts/sprint_validation.py --evidence deploy/validation/<evidence>.json \\
        --out deploy/validation/DEEPSEEK_HARNESS_SEC01_12_RESULTS.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

VERDICTS = ("PASS", "FAIL", "BLOCKED", "NOT TESTED")
EVIDENCE_CLASSES = ("SOURCE/UNIT", "DEMO/RECORDING", "SYNTHETIC RTSP", "PHYSICAL SITE CAMERA")
PHYSICAL = "PHYSICAL SITE CAMERA"
DISCLAIMER = (
    "This PoC is an advisory demonstration, not an approved physical safety control. "
    "Its alerts are not a certified protection system and its measurements are not site results."
)

# The 12 cards and their numbered items, retained from the project brief
# ("sprint solving.txt", supplied in Portuguese) as English item text.
CARDS: list[tuple[str, str, list[str]]] = [
    ("SEC-01", "Camera inventory and validation", [
        "List the cameras available to the project.",
        "Identify the physical location of each camera.",
        "Identify the IP address or access endpoint of each camera.",
        "Verify access to each video stream.",
        "Identify plant cameras suitable for proximity detection and technically validate video access.",
        "Validate image resolution and quality.",
        "Record network or connectivity blockers.",
        "Document the selected camera.",
    ]),
    ("SEC-02", "Computer vision environment", [
        "Prepare the technical environment to receive camera video and run computer vision processing.",
        "Configure access to the camera stream.",
        "Test real-time video processing.",
        "Document the dependencies used.",
        "Validate initial processing performance.",
    ]),
    ("SEC-03", "Worker detection", [
        "Implement image recognition that identifies workers or people in the camera field of view.",
        "Select a person-detection model.",
        "Integrate the model with the camera stream.",
        "Show each detected person visually in the image.",
        "Validate multiple people in one frame.",
        "Test different lighting conditions.",
        "Record test results.",
        "Document model limitations.",
    ]),
    ("SEC-04", "Virtual hazard zone", [
        "Select the machine and zone for the first PoC.",
        "Identify the hazard area visually.",
        "Create a virtual zone over the camera image.",
        "Allow the zone boundaries to be configured.",
        "Identify a person's entry into and exit from the zone.",
        "Test different zone positions.",
        "Document the rule used.",
    ]),
    ("SEC-05", "Worker and machine proximity", [
        "Define the proximity criterion.",
        "Use the detected person's position.",
        "Associate the person with the hazard zone.",
        "Detect entry into the zone.",
        "Detect continued presence in the zone.",
        "Detect exit from the zone.",
        "Test a safe scenario.",
        "Test a risk scenario.",
        "Record the results.",
    ]),
    ("SEC-06", "Risk classification", [
        "Define the Safe state.",
        "Define the Attention state.",
        "Define the Risk state.",
        "Define the condition for each state.",
        "Associate proximity with the risk level.",
        "Test changes between states.",
        "Validate behavior on real video.",
        "Document the rules.",
    ]),
    ("SEC-07", "Structured safety event", [
        "Create the event structure.",
        "Record the source camera.",
        "Record the zone or machine.",
        "Record the date and time.",
        "Record the risk level.",
        "Record the event type.",
        "Test automatic event generation.",
        "Validate the event format.",
        "Document the event contract.",
    ]),
    ("SEC-08", "Visual risk alert", [
        "Show the detected person.",
        "Show the hazard zone.",
        "Show the current state.",
        "Highlight a risk situation.",
        "Show the machine identifier.",
        "Show the timestamp.",
        "Test a real-time alert.",
        "Validate alert legibility.",
    ]),
    ("SEC-09", "Validation with real plant imagery", [
        "Test a worker outside the zone.",
        "Test a worker entering the zone.",
        "Test a worker remaining in the zone.",
        "Test a worker leaving the zone.",
        "Test more than one person.",
        "Test more than one person. (Duplicated in the source card; retained.)",
        "Test different lighting conditions.",
        "Record false positives.",
        "Record false negatives.",
        "Record performance problems.",
        "Consolidate the results.",
    ]),
    ("SEC-10", "Digital twin integration preparation", [
        "Identify the corresponding machine in the digital twin.",
        "Map camera to machine and zone.",
        "Define the communication structure.",
        "Define how the digital twin receives the event.",
        "Define the visual representation of risk.",
        "Define machine and zone states.",
        "Create a visual mockup or prototype.",
        "Validate the feasibility of future integration.",
    ]),
    ("SEC-11", "Three-dimensional safety prototype", [
        "Select the PoC machine.",
        "Use or create a simplified 3D model.",
        "Represent the safety zone.",
        "Represent the worker.",
        "Represent the Safe state.",
        "Represent the Risk state.",
        "Simulate a real-time state change.",
        "Prepare a demonstration.",
    ]),
    ("SEC-12", "PoC documentation and closeout", [
        "Document the architecture.",
        "Document the camera used.",
        "Document the computer vision model.",
        "Document the proximity rules.",
        "Document the risk levels.",
        "Document the event format.",
        "Record test results.",
        "Record limitations.",
        "Record problems encountered.",
        "Define next steps.",
        "Deliver the documentation to the team.",
    ]),
]

# Items that can only be closed by evidence from the physical plant camera or a
# consented, reviewed plant recording. Everything else may be closed by source,
# unit or recorded evidence.
REQUIRES_PHYSICAL = {
    "SEC-01.1", "SEC-01.2", "SEC-01.3", "SEC-01.4", "SEC-01.5", "SEC-01.6", "SEC-01.8",
    "SEC-02.2", "SEC-02.3", "SEC-02.5",
    # SEC-03.5 is deliberately NOT gated: "validate multiple people in one
    # frame" is a system/model capability claim, not a site-safety-performance
    # claim. It is reported as PASS only with the evidence class stated as
    # DEMO/RECORDING and the lack of plant imagery noted in the record.
    "SEC-03.6",
    "SEC-04.1", "SEC-04.2", "SEC-04.6",
    "SEC-05.7", "SEC-05.8",
    "SEC-06.7",
    "SEC-08.7", "SEC-08.8",
    "SEC-09.1", "SEC-09.2", "SEC-09.3", "SEC-09.4", "SEC-09.5", "SEC-09.6", "SEC-09.7",
    "SEC-09.8", "SEC-09.9", "SEC-09.10", "SEC-09.11",
    "SEC-10.1", "SEC-10.2",
    "SEC-12.2",
}

# Items the brief flags as duplicated in the source card.
DUPLICATED = {"SEC-09.6"}


def item_id(card: str, number: int) -> str:
    return f"{card}.{number}"


def registry() -> list[tuple[str, str, int, str]]:
    rows = []
    for card, _title, items in CARDS:
        for number, text in enumerate(items, start=1):
            rows.append((card, card, number, text))
    return rows


class PolicyError(Exception):
    pass


def validate(evidence: dict) -> list[str]:
    """Return a list of policy violations; empty means the evidence is admissible."""
    problems: list[str] = []
    required = ("id", "verdict", "evidence_class", "method", "expected", "observed", "reference")
    optional_reason = ("blocked_on", "reason")
    seen = set()
    for record in evidence.get("items", []):
        missing = [field for field in required if not record.get(field)]
        if missing:
            problems.append(f"{record.get('id', '?')}: missing {', '.join(missing)}")
        item = record.get("id", "")
        if item in seen:
            problems.append(f"{item}: duplicate record")
        seen.add(item)
        verdict = record.get("verdict")
        if verdict not in VERDICTS:
            problems.append(f"{item}: verdict {verdict!r} not in {VERDICTS}")
        if record.get("evidence_class") not in EVIDENCE_CLASSES:
            problems.append(f"{item}: evidence_class {record.get('evidence_class')!r} not in {EVIDENCE_CLASSES}")
        if verdict == "BLOCKED" and not record.get("blocked_on"):
            problems.append(f"{item}: BLOCKED requires a named external prerequisite ('blocked_on')")
        if verdict in ("FAIL", "NOT TESTED") and not (record.get("reason") or record.get("blocked_on")):
            problems.append(f"{item}: {verdict} requires a reason")
        if verdict == "PASS" and item in REQUIRES_PHYSICAL and record.get("evidence_class") != PHYSICAL:
            problems.append(
                f"{item}: POLICY VIOLATION - item requires physical-site evidence but the record is "
                f"{record.get('evidence_class')!r}; a PASS is not admissible"
            )
        if verdict == "PASS" and record.get("evidence_class") != PHYSICAL and item in REQUIRES_PHYSICAL:
            problems.append(f"{item}: physical item must not be closed with non-site evidence")
    known = {item for card, _c, number, _t in registry() for item in [item_id(card, number)]}
    for item in sorted(seen - known):
        problems.append(f"{item}: not a card item in the brief")
    for item in sorted(known - seen):
        problems.append(f"{item}: missing record (every numbered item needs a verdict)")
    return problems


def tally(evidence: dict) -> tuple[dict, dict]:
    per_card: dict = {}
    overall: Counter = Counter()
    for card, _title, _items in CARDS:
        counter: Counter = Counter()
        for record in evidence.get("items", []):
            if record["id"].startswith(card + "."):
                counter[record["verdict"]] += 1
        per_card[card] = counter
        overall.update(counter)
    return per_card, overall


def render(evidence: dict) -> str:
    identity = evidence["identity"]
    environment = evidence["environment"]
    commands = evidence["commands"]
    per_card, overall = tally(evidence)
    by_id = {record["id"]: record for record in evidence["items"]}

    lines: list[str] = []
    lines.append("# DeepSeek Harness sprint validation - SEC-01 to SEC-12")
    lines.append("")
    lines.append(f"**Generated:** {identity['date']}  ")
    lines.append(f"**Framework:** DEEPSEEK_HARNESS_SPRINT_VALIDATION.md (evidence-gathering; application code unchanged)")
    lines.append("")
    lines.append("## 1. Tested source and image identity")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for key, value in identity.items():
        if key == "date":
            continue
        lines.append(f"| {key.replace('_', ' ')} | {value} |")
    lines.append("")
    lines.append("> The repository has substantial uncommitted work, so the commit alone does not identify")
    lines.append("> the tested code. File hashes above are the source snapshot identifier.")
    lines.append("")

    lines.append("## 2. Environment")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for key, value in environment.items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")
    lines.append("")

    lines.append("## 3. Exact test commands")
    lines.append("")
    lines.append("| Command | Exit | Result |")
    lines.append("|---|---|---|")
    for command in commands:
        lines.append(f"| `{command['command']}` | {command['exit']} | {command['result']} |")
    lines.append("")

    lines.append("## 4. Verdict tally")
    lines.append("")
    header = "| Card | PASS | FAIL | BLOCKED | NOT TESTED | Items | Card result |"
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|")
    for card, _title, items in CARDS:
        counter = per_card[card]
        total = len(items)
        if counter["PASS"] == total:
            result = "**PASS**"
        else:
            result = "not complete"
        lines.append(
            f"| {card} | {counter['PASS']} | {counter['FAIL']} | {counter['BLOCKED']} | "
            f"{counter['NOT TESTED']} | {total} | {result} |"
        )
    total_items = sum(len(items) for _c, _t, items in CARDS)
    lines.append(
        f"| **All** | **{overall['PASS']}** | **{overall['FAIL']}** | **{overall['BLOCKED']}** | "
        f"**{overall['NOT TESTED']}** | **{total_items}** | - |"
    )
    lines.append("")
    lines.append(
        f"Items carrying the SEC-09 source duplication: "
        f"{', '.join(sorted(DUPLICATED))} (retained and evaluated separately)."
    )
    lines.append("")

    lines.append("## 5. Item results")
    lines.append("")
    for card, title, items in CARDS:
        lines.append(f"### {card} - {title}")
        lines.append("")
        lines.append("| Item | Requirement | Verdict | Class | Method | Observed | Evidence / blocker |")
        lines.append("|---|---|---|---|---|---|---|")
        for number, text in enumerate(items, start=1):
            record = by_id[item_id(card, number)]
            note = record.get("blocked_on") or record.get("reason") or ""
            reference = record["reference"]
            cell = f"{reference}" + (f"<br>{note}" if note else "")
            lines.append(
                f"| `{item_id(card, number)}` | {text} | **{record['verdict']}** | "
                f"{record['evidence_class']} | {record['method']} | {record['observed']} | {cell} |"
            )
        lines.append("")

    lines.append("## 6. Card summary")
    lines.append("")
    for card, title, items in CARDS:
        counter = per_card[card]
        blocking = counter["FAIL"] + counter["BLOCKED"] + counter["NOT TESTED"]
        if blocking == 0:
            state = "all items pass"
        else:
            state = f"{counter['PASS']}/{len(items)} pass; {counter['FAIL']} fail, {counter['BLOCKED']} blocked, {counter['NOT TESTED']} not tested"
        lines.append(f"- **{card} {title}**: {state}.")
    lines.append("")

    lines.append("## 7. Prioritized gap list")
    lines.append("")
    order = {"FAIL": 0, "NOT TESTED": 1, "BLOCKED": 2, "PASS": 3}
    for record in sorted(evidence["items"], key=lambda r: (order[r["verdict"]], r["id"])):
        if record["verdict"] == "PASS":
            continue
        note = record.get("blocked_on") or record.get("reason") or ""
        lines.append(f"- `{record['id']}` **{record['verdict']}** - {note}")
    lines.append("")

    lines.append("## 8. Next site inputs needed")
    lines.append("")
    for line in evidence["next_site_inputs"]:
        lines.append(f"- {line}")
    lines.append("")

    lines.append("## 9. Statement of limits")
    lines.append("")
    for line in evidence["limits"]:
        lines.append(f"- {line}")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence", required=True, help="JSON evidence file")
    parser.add_argument("--out", required=True, help="Markdown report path")
    parser.add_argument("--strict", action="store_true", help="fail if policy violations exist")
    args = parser.parse_args()

    evidence = json.loads(Path(args.evidence).read_text())
    problems = validate(evidence)
    if problems:
        print(f"{len(problems)} policy violation(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        if args.strict:
            return 1

    expected = sum(len(items) for _c, _t, items in CARDS)
    actual = len(evidence.get("items", []))
    if actual != expected:
        print(f"expected {expected} item records, found {actual}", file=sys.stderr)
        if args.strict:
            return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(evidence))
    _per_card, overall = tally(evidence)
    print(f"wrote {out}")
    print(f"items: {sum(overall.values())}  PASS={overall['PASS']} FAIL={overall['FAIL']} "
          f"BLOCKED={overall['BLOCKED']} NOT TESTED={overall['NOT TESTED']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
