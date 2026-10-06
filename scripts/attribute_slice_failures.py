"""Attribute the failing tests in a sub-project slice log to a root cause.

Reads the output of ``scripts/run_project_tests.py`` and classifies every failing
test against the exclusions the repository documents in ``SOURCE_BUNDLE.md``
(restored recordings, Roboflow splits, absent upstream commit). Anything that does
not match a known exclusion is printed as UNCLASSIFIED so it can be reviewed —
that is the signal that a decomposition boundary, not the environment, broke.

Usage:
    python scripts/run_project_tests.py all --keep-going > projects/evidence/slices-final.txt 2>&1
    python scripts/attribute_slice_failures.py projects/evidence/slices-final.txt
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

RULES: list[tuple[str, list[str]]] = [
    (
        "UPSTREAM-COMMIT-ABSENT",
        [
            "upstream_blob",
            "must exist in the original repository",
            "differs from the original repository",
            "'D:/Projects' not found in ''",
            "!= b''",
            "original really did carry an absolute path",
        ],
    ),
    (
        "DATASET-SPLITS-ABSENT",
        [
            "roboflow_downloaded",
            "307",
            "image label parity",
            "dataset splits",
            "dataset path is portable",
            "datasets expose splits",
            "dataset endpoints",
            "assertEqual(payload",
        ],
    ),
    (
        "RESTORED-MEDIA-ABSENT",
        [
            "Demo not found", "Media not found", "real_videos", "test_videos", "restored",
            "StopIteration", "scenario", "annotated clip", "10810477", "Worker_in_excavator",
            "crew_site_video", "smallest recordings", "duplicate recordings", "showcase",
            "missing LFS", "LFS REMOVED", "media catalog", "not found in set()",
            "not found in {}", "playable recording", "playback", "prewarm",
            "detection cache", "ghost", "clip", "recording", "404 != 200", "500 != 200",
            "original_media", "aliases", "duplicates", "not greater than or equal to",
        ],
    ),
]


def blocks(lines: list[str]):
    pid = None
    index = 0
    while index < len(lines):
        match = re.match(r"=== (\S+) ===", lines[index])
        if match:
            pid = match.group(1)
        if lines[index].startswith(("FAIL:", "ERROR:")):
            header, body, cursor = lines[index], [], index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if candidate.startswith(("FAIL:", "ERROR:", "=== ", "Ran ")) or candidate.startswith(("OK", "FAILED")):
                    break
                if candidate.strip() and set(candidate.strip()) != {"-"}:
                    body.append(candidate)
                cursor += 1
            yield pid, header, "\n".join(body)
            index = cursor
            continue
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", default="projects/evidence/slices-final.txt")
    args = parser.parse_args()
    lines = Path(args.log).read_text(errors="replace").splitlines()

    counts: collections.Counter[tuple[str | None, str]] = collections.Counter()
    unclassified: list[tuple[str | None, str, str]] = []
    for pid, header, body in blocks(lines):
        text = header + "\n" + body
        cause = next((name for name, keys in RULES if any(k in text for k in keys)), None)
        counts[(pid, cause or "UNCLASSIFIED")] += 1
        if not cause:
            unclassified.append((pid, header, body[-300:]))

    print(f"Failure attribution — {args.log}")
    print("=" * 78)
    print(f"{'sub-project':<22}{'root cause':<26}{'tests':>6}")
    for (pid, cause), number in sorted(counts.items()):
        print(f"{pid or '?':<22}{cause:<26}{number:>6}")
    print("\ntotals by cause")
    for cause, number in collections.Counter(c for _, c in counts.elements()).most_common():
        print(f"  {cause:<26}{number:>6}")
    print(f"\nfailing tests attributed: {sum(counts.values())}")
    print(f"unclassified:             {len(unclassified)}")
    for pid, header, tail in unclassified:
        print(f"\n  {pid}: {header[:100]}")
        print("    " + tail.replace("\n", "\n    ")[-400:])
    print(
        "\nA non-zero unclassified count means the failure is not explained by the exclusions in\n"
        "SOURCE_BUNDLE.md and must be reviewed as a possible decomposition or product defect."
    )
    return 1 if unclassified else 0


if __name__ == "__main__":
    sys.exit(main())
