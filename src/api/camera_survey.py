"""SEC-01 camera survey records, quality evidence, and selected-camera documentation."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# These are PoC screening thresholds, not a claim about a site's safety requirements.
QUALITY_THRESHOLDS = {
    "min_width": 640,
    "min_height": 480,
    "min_sharpness_laplacian": 50.0,
    "dark_mean_luma": 20.0,
    "bright_mean_luma": 235.0,
    "min_contrast_std": 15.0,
}

DISPOSITION_STATUSES = ("open", "reviewed", "approved", "rejected", "deferred")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rtsp_endpoint(url: str) -> str:
    """Return a documented endpoint without userinfo, query, or fragment."""
    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"


def _safe_text(value, limit=600):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\r\n]+", " ", value).strip()[:limit]


def endpoint_parts(draft) -> tuple[str, int | None, str]:
    """Return host, port, and path for structured or legacy camera drafts."""
    if getattr(draft, "host", ""):
        return draft.host, draft.rtsp_port, draft.stream_path or "/"
    parsed = urlsplit(getattr(draft, "rtsp_url", "") or "")
    return (parsed.hostname or "", parsed.port or (554 if parsed.scheme else None), parsed.path or "")


def _public_result(result: dict) -> dict:
    allowed = ("ok", "stage", "message", "width", "height", "quality", "blocker", "blocker_detail",
               "transport", "host", "rtsp_port", "stream_path", "access_path", "tcp_reachable",
               "tunnel_established")
    return {key: result[key] for key in allowed if key in result}


class SurveyStore:
    """Small atomic, private JSON store for SEC-01 survey attempts."""

    lock = threading.RLock()

    def __init__(self):
        self.path = Path(os.getenv("SENTINEL_SURVEY_FILE", "data/surveys/camera_survey.json"))

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "selected_camera_id": None, "selected_camera": None, "selected_at": None, "records": []}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as error:
            raise ValueError("The camera survey file could not be read") from error
        if not isinstance(data, dict) or not isinstance(data.get("records", []), list):
            raise ValueError("The camera survey file has an invalid format")
        data.setdefault("version", 1)
        data.setdefault("selected_camera_id", None)
        data.setdefault("selected_camera", None)
        data.setdefault("selected_at", None)
        return data

    def _write(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".survey-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _sec01_status(selected, records):
        selected_record = next((item for item in records if item.get("record_id") == selected), None)
        inventory_fields = ("location", "host", "rtsp_port", "stream_path")
        inventory_recorded = any(all(item.get(field) for field in inventory_fields) for item in records)
        stream_validated = any((item.get("result", {}).get("ok") and item.get("result", {}).get("width") and item.get("result", {}).get("height")) for item in records)
        blocked = [item for item in records if not item.get("result", {}).get("ok")]
        blocks_reviewed = all(item.get("disposition", {}).get("status", "open") != "open" for item in blocked)
        selected_valid = bool(selected_record and all(selected_record.get(field) for field in inventory_fields))
        requirements = {
            "inventory_recorded": inventory_recorded,
            "stream_validated": stream_validated,
            "blocked_attempts_reviewed": blocks_reviewed,
            "selected_camera": selected_valid,
        }
        labels = {
            "inventory_recorded": "record at least one camera with location, host, port, and stream path",
            "stream_validated": "receive and record a decodable video frame",
            "blocked_attempts_reviewed": "review every blocked attempt",
            "selected_camera": "select the documented PoC camera",
        }
        missing = [labels[key] for key, value in requirements.items() if not value]
        return {"complete": not missing, "requirements": requirements, "missing": missing}

    def public(self) -> dict:
        with self.lock:
            data = self._read()
            selected = data.get("selected_camera_id")
            records = []
            for record in reversed(data["records"]):
                item = dict(record)
                item["selected"] = item.get("record_id") == selected
                records.append(item)
            return {
                "version": data["version"],
                "card_id": "SEC-01",
                "selected_camera_id": selected,
                "selected_camera": data.get("selected_camera"),
                "selected_at": data.get("selected_at"),
                "sec01_status": self._sec01_status(selected, records),
                "records": records,
                "quality_thresholds": QUALITY_THRESHOLDS,
            }

    def add(self, *, draft, result: dict) -> dict:
        host, port, path = endpoint_parts(draft)
        record = {
            "record_id": f"survey-{uuid.uuid4().hex[:12]}",
            "card_id": "SEC-01",
            "checked_at": utc_now(),
            "camera_id": draft.camera_id,
            "site_id": draft.site_id,
            "location": _safe_text(getattr(draft, "location", ""), 160),
            "host": host,
            "rtsp_port": port,
            "stream_path": path,
            "access_path": _safe_text(getattr(draft, "access_path", ""), 64),
            "rtsp_endpoint": rtsp_endpoint(draft.resolved_rtsp_url()),
            "ssh_tunnel": _safe_text(draft.ssh_tunnel, 512),
            "transport": "ssh_tunnel" if draft.ssh_tunnel else "direct",
            "disposition": {"status": "open", "note": "", "updated_at": utc_now()},
            "result": _public_result(result),
        }
        with self.lock:
            data = self._read()
            data["records"].append(record)
            data["records"] = data["records"][-500:]
            self._write(data)
        return record

    def set_disposition(self, record_id: str, status: str, note: str = '') -> dict:
        if status not in DISPOSITION_STATUSES:
            raise ValueError('Disposition must be one of: ' + ', '.join(DISPOSITION_STATUSES))
        with self.lock:
            data = self._read()
            match = next((item for item in data["records"] if item.get("record_id") == record_id), None)
            if match is None:
                raise KeyError("Survey record not found")
            match["disposition"] = {
                "status": status,
                "note": _safe_text(note, 600),
                "updated_at": utc_now(),
            }
            self._write(data)
            return match

    def select(self, record_id: str) -> dict:
        with self.lock:
            data = self._read()
            match = next((item for item in data["records"] if item.get("record_id") == record_id), None)
            if match is None:
                raise KeyError("Survey record not found")
            result = match.get("result", {})
            if not result.get("ok") or not result.get("width") or not result.get("height"):
                raise ValueError("Only a camera that returned a video frame can be selected")
            required_inventory = {
                "location": "physical location",
                "host": "IP address or hostname",
                "rtsp_port": "RTSP port",
                "stream_path": "stream path",
            }
            missing_inventory = [label for key, label in required_inventory.items() if not match.get(key)]
            if missing_inventory:
                raise ValueError("Selected camera inventory requires: " + ", ".join(missing_inventory))
            data["selected_camera_id"] = record_id
            data["selected_at"] = utc_now()
            data["selected_camera"] = {key: match.get(key) for key in (
                "card_id", "camera_id", "site_id", "location", "host", "rtsp_port",
                "stream_path", "access_path", "rtsp_endpoint", "ssh_tunnel", "transport", "disposition",
            )}
            self._write(data)
            return match

    def report_data(self) -> dict:
        with self.lock:
            data = self._read()
            selected = data.get("selected_camera_id")
            records = []
            for record in reversed(data["records"]):
                item = dict(record)
                item["selected"] = item.get("record_id") == selected
                records.append(item)
            return {
                "card_id": "SEC-01",
                "generated_at": utc_now(),
                "selected_camera_id": selected,
                "selected_camera": data.get("selected_camera"),
                "selected_at": data.get("selected_at"),
                "sec01_status": self._sec01_status(selected, records),
                "records": records,
                "quality_thresholds": QUALITY_THRESHOLDS,
            }

    def report(self) -> str:
        with self.lock:
            data = self._read()
            selected = data.get("selected_camera_id")
            records = list(reversed(data["records"]))
        status = self._sec01_status(selected, records)
        lines = [
            "# PoC camera survey report",
            "",
            f"Generated: {utc_now()}",
            "",
            "This report documents SEC-01 camera survey attempts. It contains no camera passwords, private keys, or stream query strings.",
            "",
            "## SEC-01 readiness",
            f"- Complete: `{'yes' if status['complete'] else 'no'}`",
            f"- Missing: `{'; '.join(status['missing']) or 'none'}`",
            "",
            "## Selected camera",
        ]
        chosen = next((item for item in records if item.get("record_id") == selected), None)
        if chosen:
            result = chosen.get("result", {})
            quality = result.get("quality", {})
            lines.extend([
                f"- Card: `SEC-01`",
                f"- Camera ID: `{chosen.get('camera_id', '')}`",
                f"- Site ID: `{chosen.get('site_id', '')}`",
                f"- Location: `{chosen.get('location', '') or 'not recorded'}`",
                f"- IP/host: `{chosen.get('host', '') or 'not recorded'}`",
                f"- RTSP port: `{chosen.get('rtsp_port', '—')}`",
                f"- Stream path: `{chosen.get('stream_path', '') or 'not recorded'}`",
                f"- Access path: `{chosen.get('access_path', '') or 'direct'}`",
                f"- Transport: `{chosen.get('transport', 'direct')}`",
                f"- TCP reachable: `{result.get('tcp_reachable', 'n/a')}`",
                f"- SSH tunnel established: `{result.get('tunnel_established', 'n/a')}`",
                f"- Endpoint: `{chosen.get('rtsp_endpoint', '') or 'not recorded'}`",
                f"- Checked: `{chosen.get('checked_at', '')}`",
                f"- Resolution: `{result.get('width', '—')} × {result.get('height', '—')}`",
                f"- Quality status: `{quality.get('status', 'not measured')}`",
                f"- Quality notes: `{'; '.join(quality.get('notes', [])) or 'none'}`",
                f"- Disposition: `{chosen.get('disposition', {}).get('status', 'open')}`",
                f"- Connectivity: `{'validated' if result.get('ok') else 'blocked'}`",
            ])
        else:
            lines.append("- No camera has been selected yet.")
        lines.extend([
            "",
            "## Survey attempts",
            "",
            "| Checked | Camera | Location | IP/host:port | Access | TCP | Resolution | Quality | Result | Disposition | Record |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ])
        for item in records:
            result = item.get("result", {})
            quality = result.get("quality", {})
            resolution = f"{result.get('width', '—')}×{result.get('height', '—')}"
            outcome = "validated" if result.get("ok") else f"blocked: {result.get('blocker', 'unknown')}"
            tcp_value = result.get("tcp_reachable", item.get("tcp_reachable"))
            tcp_display = "reachable" if tcp_value is True else "unreachable" if tcp_value is False else "n/a"
            disposition = item.get("disposition", {}).get("status", "open")
            values = [
                item.get("checked_at", ""), item.get("camera_id", ""), item.get("location", "") or "—",
                f"{item.get('host', '') or '—'}:{item.get('rtsp_port', '—')}",
                item.get("access_path", "") or item.get("transport", "direct"), tcp_display,
                resolution, quality.get("status", "not measured"), outcome, disposition, item.get("record_id", ""),
            ]
            lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
        lines.extend([
            "",
            "## Screening thresholds",
            "",
            "The survey reports objective frame measurements. Thresholds are PoC screening values and must be replaced by site acceptance criteria before safety sign-off.",
            "",
        ])
        for key, value in QUALITY_THRESHOLDS.items():
            lines.append(f"- `{key}`: `{value}`")
        lines.extend([
            "",
            "The application cannot discover plant cameras or bypass network controls automatically. Enter each candidate endpoint, validate it, review any logged connectivity block, and select the technically validated camera for the PoC.",
            "",
        ])
        return "\n".join(lines)
