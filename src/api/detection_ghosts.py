"""Ghost detection suppression for restored recordings.

Two artefacts were measured on the restored clips, and both put a person or a
vehicle on the map that is not there as a separate object:

* **Fragmented plant.** A single excavator was reported as three separate
  ``HEAVY_EQUIPMENT`` boxes - its boom, its tracks and its body - so one machine
  became three agents on the ground plane and three proximity comparisons.
* **The cab operator.** A ``WORKER`` box lying almost entirely inside a machine
  box. The person is real and stays on the camera frame with their PPE, but
  they are not a second agent standing beside their own machine, and grading
  their distance against it invents a near-miss that did not happen.

Nothing here deletes a person from the picture. The operator's box is still
drawn and still carries its hardhat and vest notes; it is only marked, so the
tracker stops turning one human being into two agents.
"""
from typing import Any, Dict, List, Sequence, Tuple

#: Boxes of the same class that overlap by more than this share of the smaller
#: box are one object seen as parts of itself.
FRAGMENT_OVERLAP = 0.35
#: Two boxes of the same class overlapping by more than this are the same object.
DUPLICATE_OVERLAP = 0.60
#: A person inside more than this much of a machine box is that machine's
#: operator rather than a worker beside it.
OPERATOR_CONTAINMENT = 0.80

#: The class ids are the project's, imported rather than restated, so a change
#: to the mapping cannot leave this module testing the wrong classes.
from src.perception.frame_calibration import HEAVY_EQUIPMENT, LIGHT_VEHICLE, WORKER  # noqa: E402

#: The role of a detection, returned alongside the boxes rather than appended to
#: them. A positional marker was fragile: the packet already carries a track id
#: at index 6, and writing a role into a row shifted it.
ROLE_OPERATOR = "operator"


def _extent(box: Sequence[float]) -> Tuple[float, float, float, float]:
    return float(box[0]), float(box[1]), float(box[2]), float(box[3])


def _area(box: Sequence[float]) -> float:
    x1, y1, x2, y2 = _extent(box)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _overlap_ratio(inner: Sequence[float], outer: Sequence[float]) -> float:
    """Share of ``inner`` that falls inside ``outer``."""
    area = _area(inner)
    if area <= 0:
        return 0.0
    ax1, ay1, ax2, ay2 = _extent(inner)
    bx1, by1, bx2, by2 = _extent(outer)
    width = min(ax2, bx2) - max(ax1, bx1)
    height = min(ay2, by2) - max(ay1, by1)
    if width <= 0 or height <= 0:
        return 0.0
    return (width * height) / area


def suppress_ghosts(boxes: Sequence[Sequence[float]]) -> Tuple[List[List[float]], List[int]]:
    """Merge fragmented plant and duplicates; mark cab operators in place.

    An operator is flagged rather than dropped, so the person still appears on
    the camera frame with their PPE notes. Returns the rows; the input is not
    mutated. Use :func:`operator_indices` to read the flags back.
    """
    rows: List[List[float]] = [list(box) for box in boxes]
    if not rows:
        return rows, []

    # Which rows are absorbed by a larger, more confident box of the same class.
    absorbed = set()
    for i, first in enumerate(rows):
        if i in absorbed:
            continue
        for j, second in enumerate(rows):
            if i == j or j in absorbed or int(first[5]) != int(second[5]):
                continue
            if int(first[5]) == WORKER:
                # Two people overlapping is a crowd, not one object. Never merge
                # or drop a person on the strength of an overlap.
                continue
            share = _overlap_ratio(first, second)
            if share < FRAGMENT_OVERLAP:
                continue
            # Keep the box that covers the most area; on a tie the more
            # confident one, so a fragment cannot outvote the whole object.
            if _area(second) > _area(first) or (
                    _area(second) == _area(first) and float(second[4]) > float(first[4])):
                absorbed.add(i)
                break

    kept = [row for index, row in enumerate(rows) if index not in absorbed]

    # Identify a person riding inside a machine. Checked on the merged set, so a
    # boom that looked like a machine cannot make the operator look contained.
    operators: List[int] = []
    for position, box in enumerate(kept):
        if int(box[5]) != WORKER:
            continue
        for machine in kept:
            if int(machine[5]) == WORKER:
                continue
            if _overlap_ratio(box, machine) > OPERATOR_CONTAINMENT:
                operators.append(position)
                break
    return kept, operators


#: Column of the role in a served detection row: [x1,y1,x2,y2,conf,class,track,role]
ROLE_COLUMN = 7
TRACK_COLUMN = 6


def is_operator_row(detection: Sequence[float]) -> bool:
    return len(detection) > ROLE_COLUMN and detection[ROLE_COLUMN] == ROLE_OPERATOR


def summarise(before: Sequence[Sequence[float]], after: Sequence[Sequence[float]],
              operators: Sequence[int] = ()) -> Dict[str, Any]:
    """Counts for the validation record: what was merged and what was marked."""
    return {
        "detections_in": len(before),
        "detections_out": len(after),
        "plant_merged": max(0, len(before) - len(after)),
        "operators_marked": len(operators),
    }
