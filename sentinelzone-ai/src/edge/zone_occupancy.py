"""Zone occupancy tracking: entry, continued presence and exit.

The risk engine evaluates zone membership per frame with no memory, so an entry
cannot be told apart from steady occupancy and an exit is never observed. This
module keeps the missing state, and emits explicit transitions.

Occupancy is tracked per (zone, agent) pair. A transition is emitted when:

* the agent's footpoint enters the zone polygon -> ``ENTERED``
* the agent stays inside beyond the presence heartbeat -> ``STILL_INSIDE``,
  which is the observable evidence for "continued presence"
* the agent leaves the polygon -> ``EXITED``, carrying how long it dwelled and
  the highest risk level reached while inside
* the agent is no longer tracked (lost, or the stream stopped) for longer than
  the staleness window -> ``EXITED`` with reason ``track_lost``, so occupancy
  can never stay latched forever
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from shapely.geometry import Point, Polygon

#: How long a heartbeat repeats while an agent remains inside a zone.
PRESENCE_HEARTBEAT_SECONDS = 1.0
#: An agent unseen for longer than this is treated as having left the zone.
TRACK_STALE_SECONDS = 2.0


@dataclass
class ZoneTransition:
    """One observed change of zone occupancy."""

    event_type: str
    zone_id: str
    zone_name: str
    agent_id: str
    agent_class: str
    inside: bool
    dwell_seconds: float = 0.0
    entered_at: float = 0.0
    peak_risk_level: str = ""
    position: Tuple[float, float] = (0.0, 0.0)
    reason: str = "transition"
    min_ttc_seconds: float = -1.0
    collision_probability: float = 0.0

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "agent_id": self.agent_id,
            "agent_class": self.agent_class,
            "inside": self.inside,
            "dwell_seconds": round(self.dwell_seconds, 3),
            "peak_risk_level": self.peak_risk_level,
            "position": [round(self.position[0], 3), round(self.position[1], 3)],
            "reason": self.reason,
            "min_ttc_seconds": self.min_ttc_seconds,
            "collision_probability": self.collision_probability,
        }


@dataclass
class _Occupancy:
    zone_id: str
    zone_name: str
    agent_id: str
    agent_class: str
    entered_at: float
    last_seen_inside: float
    last_presence_at: float
    last_seen: float
    peak_risk_level: str = ""
    peak_ttc: float = -1.0
    peak_probability: float = 0.0
    min_ttc_seconds: float = -1.0
    collision_probability: float = 0.0
    position: Tuple[float, float] = (0.0, 0.0)
    risks: List[str] = field(default_factory=list)

    def note_risk(self, risk: Optional[dict], now: float) -> None:
        """Remember the strongest risk seen while this agent is inside."""
        if not risk:
            return
        level = str(risk.get("state", ""))
        if level and self.peak_risk_level in ("", "NORMAL_LEVEL_0"):
            self.peak_risk_level = level
        elif level and _higher(level, self.peak_risk_level):
            self.peak_risk_level = level
        ttc = float(risk.get("min_ttc", -1.0))
        if ttc >= 0:
            self.min_ttc_seconds = ttc if self.min_ttc_seconds < 0 else min(self.min_ttc_seconds, ttc)
        self.collision_probability = max(self.collision_probability, float(risk.get("p_col", 0.0)))


_LEVELS = {"NORMAL_LEVEL_0": 0, "ADVISORY_LEVEL_1": 1, "WARNING_LEVEL_2": 2, "CRITICAL_LEVEL_3": 3}


def _higher(candidate: str, current: str) -> bool:
    return _LEVELS.get(candidate, 0) > _LEVELS.get(current, 0)


class ZoneOccupancyTracker:
    """Tracks per-zone occupancy and emits entry, presence and exit transitions."""

    def __init__(
        self,
        presence_heartbeat_seconds: float = PRESENCE_HEARTBEAT_SECONDS,
        track_stale_seconds: float = TRACK_STALE_SECONDS,
    ) -> None:
        self.presence_heartbeat_seconds = presence_heartbeat_seconds
        self.track_stale_seconds = track_stale_seconds
        self._inside: Dict[Tuple[str, str], _Occupancy] = {}
        self._last_seen: Dict[str, float] = {}

    # -- introspection -------------------------------------------------
    def occupancy(self) -> List[dict]:
        """Current occupancy, one record per (zone, agent) pair inside a zone."""
        return [
            {
                "zone_id": item.zone_id,
                "zone_name": item.zone_name,
                "agent_id": item.agent_id,
                "agent_class": item.agent_class,
                "dwell_seconds": round(max(0.0, time.time() - item.entered_at), 3),
                "peak_risk_level": item.peak_risk_level,
            }
            for item in self._inside.values()
        ]

    def is_inside(self, zone_id: str, agent_id: str) -> bool:
        return (zone_id, agent_id) in self._inside

    # -- update --------------------------------------------------------
    def update(
        self,
        envelopes: Iterable[dict],
        agents: Iterable[dict],
        risks: Optional[Dict[str, dict]] = None,
        now: Optional[float] = None,
    ) -> List[ZoneTransition]:
        """Reconcile the tracked agents against the zone polygons.

        ``agents`` yields ``{"agent_id", "class_name", "position"}`` where
        position is the agent's ground footpoint in metric coordinates.
        ``risks`` maps agent_id to the pair-risk dict for that agent.
        """
        moment = time.time() if now is None else now
        risks = risks or {}
        transitions: List[ZoneTransition] = []

        polygons = []
        for envelope in envelopes:
            coords = envelope.get("polygon") or envelope.get("polygon_metric_epsg3857")
            if not coords or len(coords) < 3:
                continue
            zone_id = str(envelope.get("envelope_id") or envelope.get("zone_id") or "")
            if not zone_id:
                continue
            polygons.append(
                (
                    zone_id,
                    str(envelope.get("zone_name") or zone_id),
                    Polygon(coords),
                )
            )

        seen_pairs = set()
        for agent in agents:
            agent_id = str(agent.get("agent_id"))
            agent_class = str(agent.get("class_name") or "")
            position = agent.get("position")
            if position is None:
                continue
            x, y = float(position[0]), float(position[1])
            point = Point(x, y)
            risk = risks.get(agent_id)
            self._last_seen[agent_id] = moment

            inside_now = {
                zone_id
                for zone_id, _name, polygon in polygons
                if polygon.covers(point)
            }
            for zone_id, zone_name, _polygon in polygons:
                key = (zone_id, agent_id)
                seen_pairs.add(key)
                if zone_id in inside_now:
                    transitions.extend(self._on_inside(key, zone_name, agent_id, agent_class, risk, moment, (x, y)))
                else:
                    transitions.extend(self._on_outside(key, zone_name, agent_id, agent_class, moment, (x, y)))

        transitions.extend(self._expire(moment, seen_pairs))
        return transitions

    # -- internals -----------------------------------------------------
    def _on_inside(
        self,
        key: Tuple[str, str],
        zone_name: str,
        agent_id: str,
        agent_class: str,
        risk: Optional[dict],
        now: float,
        position: Tuple[float, float],
    ) -> List[ZoneTransition]:
        zone_id = key[0]
        record = self._inside.get(key)
        if record is None:
            record = _Occupancy(
                zone_id=zone_id,
                zone_name=zone_name,
                agent_id=agent_id,
                agent_class=agent_class,
                entered_at=now,
                last_seen_inside=now,
                last_presence_at=now,
                last_seen=now,
                position=position,
            )
            record.note_risk(risk, now)
            self._inside[key] = record
            return [
                ZoneTransition(
                    event_type="ZONE_ENTRY",
                    zone_id=zone_id,
                    zone_name=zone_name,
                    agent_id=agent_id,
                    agent_class=agent_class,
                    inside=True,
                    entered_at=now,
                    peak_risk_level=record.peak_risk_level,
                    position=position,
                    reason="polygon_containment",
                    min_ttc_seconds=record.min_ttc_seconds,
                    collision_probability=record.collision_probability,
                )
            ]

        record.last_seen_inside = now
        record.last_seen = now
        record.position = position
        record.agent_class = agent_class or record.agent_class
        record.note_risk(risk, now)
        if now - record.last_presence_at >= self.presence_heartbeat_seconds:
            # Heartbeat: the observable evidence of continued presence. Emitted
            # on a fixed cadence, so the rate of presence events measures time
            # inside the zone and not the frame rate.
            record.last_presence_at = now
            return [
                ZoneTransition(
                    event_type="ZONE_OCCUPANCY",
                    zone_id=zone_id,
                    zone_name=record.zone_name,
                    agent_id=agent_id,
                    agent_class=record.agent_class,
                    inside=True,
                    dwell_seconds=now - record.entered_at,
                    entered_at=record.entered_at,
                    peak_risk_level=record.peak_risk_level,
                    position=position,
                    reason="still_inside",
                    min_ttc_seconds=record.min_ttc_seconds,
                    collision_probability=record.collision_probability,
                )
            ]
        return []

    def _on_outside(
        self,
        key: Tuple[str, str],
        zone_name: str,
        agent_id: str,
        agent_class: str,
        now: float,
        position: Tuple[float, float],
    ) -> List[ZoneTransition]:
        record = self._inside.pop(key, None)
        if record is None:
            return []
        return [
            ZoneTransition(
                event_type="ZONE_EXIT",
                zone_id=key[0],
                zone_name=record.zone_name,
                agent_id=agent_id,
                agent_class=record.agent_class,
                inside=False,
                dwell_seconds=now - record.entered_at,
                entered_at=record.entered_at,
                peak_risk_level=record.peak_risk_level,
                position=position,
                reason="left_polygon",
                min_ttc_seconds=record.min_ttc_seconds,
                collision_probability=record.collision_probability,
            )
        ]

    def _expire(self, now: float, seen_pairs: set) -> List[ZoneTransition]:
        """Close occupancies whose agent stopped being tracked."""
        transitions: List[ZoneTransition] = []
        for key, record in list(self._inside.items()):
            agent_id = key[1]
            last = self._last_seen.get(agent_id, record.last_seen)
            if key in seen_pairs and now - last <= self.track_stale_seconds:
                continue
            if now - last <= self.track_stale_seconds:
                continue
            self._inside.pop(key, None)
            transitions.append(
                ZoneTransition(
                    event_type="ZONE_EXIT",
                    zone_id=record.zone_id,
                    zone_name=record.zone_name,
                    agent_id=agent_id,
                    agent_class=record.agent_class,
                    inside=False,
                    dwell_seconds=last - record.entered_at,
                    entered_at=record.entered_at,
                    peak_risk_level=record.peak_risk_level,
                    position=record.position,
                    reason="track_lost",
                    min_ttc_seconds=record.min_ttc_seconds,
                    collision_probability=record.collision_probability,
                )
            )
        for agent_id in list(self._last_seen):
            if now - self._last_seen[agent_id] > self.track_stale_seconds * 4:
                self._last_seen.pop(agent_id, None)
        return transitions

    def reset(self) -> None:
        self._inside.clear()
        self._last_seen.clear()
