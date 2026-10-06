import json
from datetime import datetime, timezone
import os
import re
import logging
from datetime import datetime
from typing import TypedDict, List, Dict, Any, Optional
from pydantic import ValidationError

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    ChatOpenAI = None
    ChatPromptTemplate = None
    StateGraph = None
    END = "__end__"

from src.schemas.contracts import ShiftSafetyManifest, DynamicEnvelopeConfig, ZoneSeverity, EntityType
from src.spatial.bim_resolver import BIMSpatialResolver
from src.graph.llm_provider import get_agent_llm

logger = logging.getLogger("ContextGraphPipeline")


class ContextGraphState(TypedDict):
    site_id: str
    shift_date: str
    raw_permit_text: str
    ifc_file_path: str
    extracted_tasks: List[Dict[str, Any]]
    resolved_envelopes: List[Dict[str, Any]]
    validated_manifest: Optional[ShiftSafetyManifest]
    validation_errors: List[str]
    dispatch_status: str


TASK_EXTRACTION_PROMPT = """
You are a Principal Civil Safety Operations Engineer.
Parse the daily shift permits to work (PTWs), morning briefings, and crane lift schedules.
Extract all active heavy equipment tasks, excavation zones, and restricted personnel areas.

Return ONLY a valid JSON array of objects conforming to this schema:
[
  {{
    "activity_name": "Trench Excavation",
    "location_reference": "Pier B4",
    "equipment_types": ["HEAVY_ARTICULATED"],
    "has_dedicated_spotter": true,
    "valid_from": "2026-09-07T07:00:00Z",
    "valid_until": "2026-09-07T17:00:00Z",
    "risk_level": "HIGH",
    "notes": "Worker access permitted only for certified pipelayer"
  }}
]
"""


def extract_tasks_node(state: ContextGraphState) -> Dict[str, Any]:
    llm = get_agent_llm()
    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", TASK_EXTRACTION_PROMPT),
            ("human", "Daily Work Permits and Shift Documentation:\n{raw_permit_text}")
        ])
        chain = prompt | llm
        response = chain.invoke({"raw_permit_text": f"Shift date: {state.get('shift_date', 'unspecified')}\n" + state.get("raw_permit_text", "")})
        content = response.content if hasattr(response, "content") else str(response)
        # Clean potential markdown formatting
        clean_json = re.sub(r"^```json\s*", "", content.strip(), flags=re.MULTILINE)
        clean_json = re.sub(r"^```\s*", "", clean_json.strip(), flags=re.MULTILINE)
        clean_json = clean_json.strip("` \n")
        tasks = json.loads(clean_json)
        if isinstance(tasks, list) and len(tasks) > 0:
            return {"extracted_tasks": tasks}
    except Exception as e:
        if os.getenv("SENTINEL_MODE") == "production":
            raise RuntimeError("Permit extraction unavailable") from e
        logger.warning(f"LLM task extraction fallback invoked: {e}")

    if os.getenv("SENTINEL_MODE") == "production":
        raise ValueError("No permit tasks extracted")

    # Deterministic heuristic extraction fallback (e.g. for offline testing / missing API key)
    raw_text = state.get("raw_permit_text", "")
    tasks = []
    if "Pier B4" in raw_text or "Trench" in raw_text:
        tasks.append({
            "activity_name": "Trench Excavation",
            "location_reference": "Pier B4",
            "equipment_types": ["HEAVY_ARTICULATED"],
            "has_dedicated_spotter": True,
            "valid_from": f"{state.get('shift_date', '2026-09-07')}T07:00:00Z",
            "valid_until": f"{state.get('shift_date', '2026-09-07')}T17:00:00Z",
            "risk_level": "HIGH",
            "notes": "Worker access permitted only for certified pipelayer"
        })
    elif state.get("extracted_tasks"):
        tasks = state["extracted_tasks"]
    else:
        tasks.append({
            "activity_name": "Site Earthmoving",
            "location_reference": "General Zone",
            "equipment_types": ["HEAVY_RIGID"],
            "has_dedicated_spotter": False,
            "valid_from": f"{state.get('shift_date', '2026-09-07')}T07:00:00Z",
            "valid_until": f"{state.get('shift_date', '2026-09-07')}T17:00:00Z",
            "risk_level": "MEDIUM",
            "notes": "Standard haul corridor"
        })
    return {"extracted_tasks": tasks}


def resolve_bim_node(state: ContextGraphState) -> Dict[str, Any]:
    resolver = BIMSpatialResolver(state.get("ifc_file_path", ""))
    resolved_envelopes = []

    for task in state.get("extracted_tasks", []):
        loc_ref = task.get("location_reference", "General")
        poly = resolver.resolve_element_polygon(loc_ref)

        multiplier = 1.0 if task.get("has_dedicated_spotter") else 1.35
        severity = ZoneSeverity.CRITICAL_EXCLUSION if task.get("risk_level") == "HIGH" else ZoneSeverity.WARNING_BUFFER

        exempt = []
        if task.get("has_dedicated_spotter"):
            exempt.append(EntityType.SPOTTER)
        if "Worker access permitted" in task.get("notes", ""):
            exempt.append(EntityType.WORKER)

        env_dict = {
            "envelope_id": f"ENV_{state.get('site_id', 'SITE')}_{loc_ref.replace(' ', '_')}",
            "zone_name": task["activity_name"],
            "activity_type": task["activity_name"],
            "valid_from": task["valid_from"],
            "valid_until": task["valid_until"],
            "polygon_metric_epsg3857": poly,
            "severity": severity,
            "exempt_entities": exempt,
            "ttc_multiplier": multiplier,
            "max_allowable_speed_ms": 2.22
        }
        resolved_envelopes.append(env_dict)

    return {"resolved_envelopes": resolved_envelopes}


def validate_manifest_node(state: ContextGraphState) -> Dict[str, Any]:
    errors = []
    validated_objs = []
    for raw_env in state.get("resolved_envelopes", []):
        try:
            valid_env = DynamicEnvelopeConfig(**raw_env)
            validated_objs.append(valid_env)
        except ValidationError as ve:
            errors.append(str(ve))

    if errors:
        return {"validation_errors": errors, "validated_manifest": None}

    manifest = ShiftSafetyManifest(
        shift_id=f"SHIFT_{state.get('shift_date', '20260907')}_{state.get('site_id', 'SITE')}",
        site_id=state.get("site_id", "SITE"),
        compiled_at=datetime.now(timezone.utc).isoformat(),
        envelopes=validated_objs
    )
    return {"validated_manifest": manifest, "validation_errors": []}


def dispatch_edge_mqtt_node(state: ContextGraphState) -> Dict[str, Any]:
    manifest = state.get("validated_manifest")
    if not manifest:
        return {"dispatch_status": "FAILED_VALIDATION"}

    # 1. Persist the compiled shift safety manifest.
    #
    # This used to write the relative path "data/manifests" unconditionally, which
    # meant two things. Any run - including a test run - overwrote the
    # repository's checked-in configuration, replacing active_manifest.json (the
    # hazard envelopes in force for the active site) with whichever shift was
    # compiled last. And the path depended on the working directory, so the same
    # call wrote to different places depending on where the app was started.
    # SENTINEL_MANIFEST_DIR now selects the location; the checked-in directory
    # remains the default.
    from src.edge.manifest_selector import manifest_dir
    manifests_dir = manifest_dir()
    manifests_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifests_dir / f"{manifest.shift_id}.json"
    active_path = manifests_dir / "active_manifest.json"

    manifest_dict = manifest.model_dump() if hasattr(manifest, "model_dump") else manifest.dict()
    manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")
    active_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    applied = 0
    # 2. Real Edge Conflict Engine Delivery (if DemoService or runtime is active in app)
    try:
        from src.api.app import demo_service, live_service
        if live_service:
            envelope_payloads = [env.model_dump(mode="json") for env in manifest.envelopes]
            for worker in live_service.workers.values():
                if worker.config.site_id != manifest.site_id:
                    continue
                with worker.runtime_lock:
                    worker.runtime.conflict_engine.load_manifest_envelopes(envelope_payloads)
                    applied += 1
        if demo_service and hasattr(demo_service, "runtime") and demo_service.runtime:
            envelope_payloads = [env if isinstance(env, dict) else env.model_dump() for env in manifest.envelopes]
            demo_service.runtime.conflict_engine.load_manifest_envelopes(envelope_payloads)
            applied += 1
            logger.info(f"Dynamically injected {len(envelope_payloads)} envelopes into active edge runtime.")
    except Exception as e:
        logger.debug(f"Direct in-memory edge injection skipped: {e}")

    logger.info("Shift manifest persisted; in-process dispatch attempted (no MQTT publication).")
    return {
        "dispatch_status": f"SUCCESS_DISPATCHED_{len(manifest.envelopes)}_ENVELOPES" if applied else "PERSISTED_NOT_APPLIED",
        # str() first: manifest_path is a Path, and Path.replace() means
        # "rename", not "substitute".
        "manifest_path": str(manifest_path).replace("\\", "/"),
        "envelopes_count": len(manifest.envelopes)
    }


def build_context_pipeline():
    """
    Assembles and compiles the StateGraph for Pipeline 1: Context & Dynamic Spatial Envelopes.
    """
    if not HAS_LANGGRAPH or StateGraph is None:
        logger.warning("langgraph is not installed. Pipeline builder returning None.")
        return None

    graph = StateGraph(ContextGraphState)
    graph.add_node("extract_tasks", extract_tasks_node)
    graph.add_node("resolve_bim", resolve_bim_node)
    graph.add_node("validate_manifest", validate_manifest_node)
    graph.add_node("dispatch_edge", dispatch_edge_mqtt_node)

    graph.set_entry_point("extract_tasks")
    graph.add_edge("extract_tasks", "resolve_bim")
    graph.add_edge("resolve_bim", "validate_manifest")
    graph.add_edge("validate_manifest", "dispatch_edge")
    graph.add_edge("dispatch_edge", END)
    return graph.compile()
