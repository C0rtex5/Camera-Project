"""Structured safety event contract.

This module defines the event the project cards require (SEC-07): a stable,
versioned structure recording the source camera, the zone or machine, the date
and time, the risk level, the event type, and whether the driving detection was
an actual detector output. Events are produced automatically by the edge
runtime on a risk transition or a zone entry/exit, not by a manual relay.

The internal engine has four severity levels. The cards name three business
states, so the mapping is declared here once, explicitly, rather than left for
each reader to infer:

    NORMAL_LEVEL_0     -> SAFE       (no interaction at risk)
    ADVISORY_LEVEL_1   -> ATTENTION  (proximity developing, below warning TTC)
    WARNING_LEVEL_2    -> RISK       (collision predicted inside the warning horizon)
    CRITICAL_LEVEL_3   -> RISK       (collision predicted inside the critical horizon)

WARNING and CRITICAL are both RISK: the business state has three values, so the
two escalation levels collapse into it and are distinguished by the retained
``risk_level`` field. Consumers that need the four-level detail read
``risk_level``; consumers that need the site vocabulary read ``business_state``.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

SCHEMA_VERSION = "sentinel.safety_event/1.0"


class RiskLevel(str, Enum):
    NORMAL = "NORMAL_LEVEL_0"
    ADVISORY = "ADVISORY_LEVEL_1"
    WARNING = "WARNING_LEVEL_2"
    CRITICAL = "CRITICAL_LEVEL_3"


class BusinessState(str, Enum):
    """The three states the project cards define."""

    SAFE = "SAFE"
    ATTENTION = "ATTENTION"
    RISK = "RISK"


#: Declared mapping from the engine's four severity levels to the three
#: business states. Documented in deploy/SAFETY_EVENT_CONTRACT.md.
BUSINESS_STATE_BY_LEVEL: Dict[RiskLevel, BusinessState] = {
    RiskLevel.NORMAL: BusinessState.SAFE,
    RiskLevel.ADVISORY: BusinessState.ATTENTION,
    RiskLevel.WARNING: BusinessState.RISK,
    RiskLevel.CRITICAL: BusinessState.RISK,
}

#: Severity ordering, lowest first.
LEVEL_ORDER: Tuple[RiskLevel, ...] = (
    RiskLevel.NORMAL,
    RiskLevel.ADVISORY,
    RiskLevel.WARNING,
    RiskLevel.CRITICAL,
)


def to_risk_level(value: str) -> RiskLevel:
    """Coerce an engine severity string to a RiskLevel, defaulting to NORMAL."""
    try:
        return RiskLevel(value)
    except ValueError:
        return RiskLevel.NORMAL


def business_state(value: str) -> BusinessState:
    return BUSINESS_STATE_BY_LEVEL[to_risk_level(value)]


class EventType(str, Enum):
    ZONE_ENTRY = "ZONE_ENTRY"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_OCCUPANCY = "ZONE_OCCUPANCY"
    RISK_ESCALATION = "RISK_ESCALATION"
    RISK_DEESCALATION = "RISK_DEESCALATION"
    CAMERA_OFFLINE = "CAMERA_OFFLINE"


class DetectionStatus(str, Enum):
    """Whether the value that drove the event came from a real detector output."""

    #: Produced by the detector on a live or recorded frame.
    MEASURED = "MEASURED"
    #: Produced from a scenario configuration that is not a surveyed site zone.
    ILLUSTRATIVE = "ILLUSTRATIVE"
    #: No detection supported the event (e.g. track loss, connectivity).
    UNAVAILABLE = "UNAVAILABLE"


class SafetyEvent(BaseModel):
    """One safety event, as required by the project cards (SEC-07)."""

    event_id: str = Field(description="Unique identifier for this event")
    schema_version: str = Field(default=SCHEMA_VERSION, description="Contract version of this record")
    event_type: EventType = Field(description="What happened")

    # Where it happened: the source camera, the site, the zone and the machine.
    source_camera_id: str = Field(description="Camera the event was observed on")
    site_id: str = Field(default="", description="Site identifier from the camera profile or manifest")
    zone_id: str = Field(default="", description="Envelope the event relates to, empty when no zone applies")
    zone_name: str = Field(default="", description="Human-readable zone name")
    machine_id: str = Field(default="", description="Machine the zone belongs to, empty when unknown")

    # When it happened.
    event_timestamp_utc: str = Field(description="When the event was raised, ISO-8601 UTC")
    frame_id: str = Field(default="", description="Identifier of the frame the event was observed on")
    frame_captured_at_utc: str = Field(default="", description="Capture time of that frame, ISO-8601 UTC")

    # What the risk was.
    risk_level: RiskLevel = Field(description="Engine severity level, all four retained")
    business_state: BusinessState = Field(description="Safe / Attention / Risk, mapped from risk_level")
    min_ttc_seconds: float = Field(default=-1.0, ge=-1.0, description="Time to collision in the forecast, -1 when clear")
    collision_probability: float = Field(default=0.0, ge=0.0, le=1.0, description="Peak mode-mass collision probability")

    # What was detected, and how much to trust it.
    detection_status: DetectionStatus = Field(description="MEASURED, ILLUSTRATIVE or UNAVAILABLE")
    detected_class: str = Field(default="", description="Internal class of the driving detection, e.g. WORKER")
    track_id: Optional[int] = Field(default=None, description="Track identifier of the driving detection")
    dwell_seconds: float = Field(default=0.0, ge=0.0, description="Time spent in the zone before an exit")
    peak_risk_level: str = Field(default="", description="Highest level reached during an occupancy or dwell period")

    # Honest provenance: this PoC is advisory, never a certified control.
    forecast_status: str = Field(default="", description="Forecast availability on the source frame")
    advisory_only: bool = Field(
        default=True,
        description="This event is an advisory demonstration output, not a certified safety control",
    )


class SafetyEventLog:
    """Appends events to a daily JSONL file and notifies subscribers.

    The file sink keeps the record durable and greppable; the callback lets the
    API broadcast the same event to connected clients, so one emission feeds
    both storage and the UI.
    """

    def __init__(
        self,
        directory: Optional[str] = None,
        sink: Optional[Callable[[SafetyEvent], None]] = None,
    ) -> None:
        if directory is None:
            directory = os.getenv("SENTINEL_EVENT_DIR", "data/incidents")
        self.directory = Path(directory)
        self.sink = sink
        self._lock = threading.Lock()
        self.recent: List[SafetyEvent] = []

    def _file_for(self, moment: datetime) -> Path:
        return self.directory / f"events-{moment.strftime('%Y%m%d')}.jsonl"

    def append(self, event: SafetyEvent) -> None:
        with self._lock:
            moment = datetime.now(timezone.utc)
            path = self._file_for(moment)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(event.model_dump_json() + "\n")
            except OSError:
                # A read-only filesystem must not take the inference loop down.
                pass
            self.recent.append(event)
            del self.recent[:-200]
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:
                pass

    def read(self, limit: int = 50) -> List[SafetyEvent]:
        with self._lock:
            return list(self.recent[-limit:])


def build_event(
    event_type: EventType,
    source_camera_id: str,
    risk_level: str,
    *,
    site_id: str = "",
    zone_id: str = "",
    zone_name: str = "",
    machine_id: str = "",
    event_timestamp: Optional[datetime] = None,
    frame_id: str = "",
    frame_captured_at: Optional[datetime] = None,
    min_ttc_seconds: float = -1.0,
    collision_probability: float = 0.0,
    detection_status: DetectionStatus = DetectionStatus.MEASURED,
    detected_class: str = "",
    track_id: Optional[int] = None,
    dwell_seconds: float = 0.0,
    peak_risk_level: str = "",
    forecast_status: str = "",
) -> SafetyEvent:
    """Build a validated event with the business-state mapping applied."""
    level = to_risk_level(risk_level)
    event_moment = event_timestamp or datetime.now(timezone.utc)
    captured = frame_captured_at or event_moment
    return SafetyEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        source_camera_id=source_camera_id,
        site_id=site_id,
        zone_id=zone_id,
        zone_name=zone_name,
        machine_id=machine_id,
        event_timestamp_utc=event_moment.astimezone(timezone.utc).isoformat(),
        frame_id=frame_id,
        frame_captured_at_utc=captured.astimezone(timezone.utc).isoformat(),
        risk_level=level,
        business_state=BUSINESS_STATE_BY_LEVEL[level],
        min_ttc_seconds=min_ttc_seconds,
        collision_probability=collision_probability,
        detection_status=detection_status,
        detected_class=detected_class,
        track_id=track_id,
        dwell_seconds=dwell_seconds,
        peak_risk_level=peak_risk_level,
        forecast_status=forecast_status,
    )


def to_broadcast_payload(event: SafetyEvent) -> Dict[str, object]:
    """Compact payload for the WebSocket broadcast, with the contract version."""
    payload = event.model_dump(mode="json")
    payload["contract"] = SCHEMA_VERSION
    return json.loads(json.dumps(payload, default=str))
