# SentinelZone-AI: Predictive Dynamic Spatial Safety & Autonomous Agentic Workzones

Production work targets one RTSP camera with CPU inference and optional NVIDIA acceleration. For stakeholder review, open the hub: bundled demo scenarios load immediately without any access token. There is no application login. Camera connections use the camera’s own username and password. Camera setup is optional. The restored split-view detection hub opens with offline demo scenarios when no camera is configured. Live monitoring uses the same camera/3D layout, with synchronized observations and separate frame-specific reviews. The app opens without camera data: use **Camera setup** (`/setup`) to save connection details now and complete calibration and models later. The SEC-01 camera survey records candidate resolution, frame-quality evidence, connectivity blocks, and the selected PoC camera in a redacted report. See [deployment instructions](deploy/PRODUCTION.md), the [SEC-01 survey procedure](deploy/CAMERA_SURVEY.md), [camera-aligned forecast training](deploy/FORECAST_TRAINING.md), and [verified results and remaining work](deploy/VALIDATION.md). The architecture and performance descriptions below include original demo claims and design targets; they are not live-site validation.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-EE4C2C?logo=pytorch&logoColor=white)](https://pytorch.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agentic%20Orchestration-1C3C3C?logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![CUDA](https://img.shields.io/badge/CUDA-RTX%204070%20Laptop%20%7C%20Jetson%20Orin-76B900?logo=nvidia&logoColor=white)](https://developer.nvidia.com/cuda-zone)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Three.js](https://img.shields.io/badge/Three.js-r128-000000?logo=three.js&logoColor=white)](https://threejs.org)
[![Tests](https://img.shields.io/badge/Tests-46%2F46%20Passing-brightgreen)](tests/)

**SentinelZone-AI** is an edge-native, predictive dynamic spatial safety and autonomous agentic platform engineered for active industrial construction, heavy civil infrastructure, and mining workzones.

Unlike conventional reactive computer vision systems that simply draw 2D bounding boxes, SentinelZone-AI bridges physical edge perception with high-level cognitive agentic reasoning:
1. **Physical Edge Layer (<120ms):** Visual detections are projected onto a calibrated geodetic metric ground plane via planar homography, tracked with Extended Kalman Filters (EKF), and projected into the future using Spatio-Temporal Graph Attention Networks (GATv2 ST-GNN) to anticipate struck-by collisions before they occur.
2. **Cognitive Agentic Layer (LangGraph ReAct):** An autonomous Site Safety Supervisor agent orchestrates permits-to-work (PTW) compilation into dynamic BIM exclusion envelopes, runs multimodal VLM incident forensic triage, curates high-loss edge cases for active learning, and synthesizes OSHA-compliant daily safety briefings. The production LLM path requires an explicitly configured provider; camera monitoring and bundled demo playback do not.

---

## Complete System Architecture

```
                                SENTINELZONE-AI ARCHITECTURE
                                
   ============================== PHYSICAL EDGE LAYER (<120ms) ==============================
   
   +----------------+       +------------------------+       +----------------------+
   | Camera Stream  | --->  | CUDA Perception Engine | --->  | Planar Homography H⁻¹|
   | (RTSP / Video) |       | YOLOv8 + PPE Analysis  |       | (2D Box -> Metric 3D)|
   +----------------+       +------------------------+       +----------+-----------+
                                                                        |
   +--------------------------------------------------------------------+
   |
   v
   +------------------+       +------------------------+       +----------------------+
   |  Multi-Agent EKF | --->  |   GATv2 ST-GNN Engine  | --->  | Dynamic Exclusion    |
   |  State Tracking  |       | Trajectory Forecasting |       | Envelopes & TTC Calc |
   +------------------+       +------------------------+       +----------+-----------+
                                                                        |
   ============================= COGNITIVE AGENTIC LAYER (LangGraph) ========================
                                                                        |
   +--------------------------------------------------------------------+
   |
   v
   +----------------------------------------------------------------------------------+
   |                  AUTONOMOUS SITE SAFETY SUPERVISOR (ReAct Agent)                 |
   |                                                                                  |
   |   [Tool 1: query_site_telemetry]       --> Real-time EKF tracks & TTC metrics    |
   |   [Tool 2: compile_work_permit]        --> Pipeline 1: NLP PTW to BIM Envelopes  |
   |   [Tool 3: adjudicate_incident_packet] --> Pipeline 2: Multimodal VLM Triage     |
   |   [Tool 4: generate_daily_toolbox_talk]--> OSHA 29 CFR 1926 Safety Briefings     |
   |   [Tool 5: get_active_learning_queue]  --> Curated Edge-Case Retraining Dataset  |
   |   [Tool 6: update_exclusion_corridor]  --> Live Collision Engine Zone Ingestion  |
   +----------------------------------------------------------------------------------+
                                         |
   =============================== PRESENTATION & CONTROL ============================
                                         v
   +----------------------------------------------------------------------------------+
   |                      FASTAPI DEMO & COPILOT GATEWAY (:8000)                      |
   |   * Interactive 3D WebGL Digital Twin (Three.js geodetic datum ground plane)    |
   |   * Canvas-rendered Video Overlay (Zero-codec failure JPEG streaming)            |
   |   * AI Safety Supervisor Chat Modal (Multi-turn ReAct with live tool traces)     |
   |   * Dynamic Zero-Config Video Ingestion (Auto-catalog data/real_videos/)         |
   +----------------------------------------------------------------------------------+
```

---

## Key Modules & Autonomous Pipelines

### 1. Autonomous Site Safety Supervisor Agent (`src/graph/supervisor_agent.py`)
- Built on LangGraph `StateGraph(SupervisorAgentState)` implementing a multi-turn ReAct (Reason + Act) loop.
- **6 Real Domain Tools (Zero Placeholders / Zero Mocks):**
  - `query_site_telemetry`: Fetches real-time kinematic tracks, observed Time-to-Collision (TTC), and near-miss escalation flags.
  - `compile_work_permit`: Triggers Pipeline 1 to parse unstructured PTWs and push dynamic exclusion polygons to the edge collision engine.
  - `adjudicate_incident_packet`: Triggers Pipeline 2 to perform multimodal forensic triage and root-cause determination.
  - `generate_daily_toolbox_talk`: Synthesizes OSHA 29 CFR 1926 compliant daily safety briefings tailored to empirical site hazard data.
  - `get_active_learning_queue`: Audits the active learning queue containing false alarms, low-confidence frames, and high-loss near-misses.
  - `update_exclusion_corridor`: Dynamically expands or contracts machinery exclusion zones in the active edge runtime.

### 2. Unified Multi-Provider LLM Engine (`src/graph/llm_provider.py`)
- Factory method `get_agent_llm()` supporting **OpenAI GPT-4o** and **Anthropic Claude 3.5 Sonnet** with automatic `.with_fallbacks([DeterministicAgentLLM()])`.
- Features `DeterministicAgentLLM` (subclass of LangChain's `BaseChatModel`), a specialized domain safety reasoner enabling complete offline edge air-gap autonomy and test determinism without external API dependencies.

### 3. Pipeline 1: Dynamic Spatial Envelopes (`src/graph/context_pipeline.py`)
- NLP Task Extraction $\rightarrow$ BIM Datum Resolution $\rightarrow$ Pydantic v2 `ShiftSafetyManifest` Validation $\rightarrow$ MQTT / Live Collision Engine Injection.
- Persists manifests to `data/manifests/{shift_id}.json` and updates `SentinelEdgeRuntime.conflict_engine` memory dynamically.

### 4. Pipeline 2: Multimodal Incident Adjudication (`src/graph/adjudication_pipeline.py`)
- Multi-factor VLM Triage (`TRUE_POSITIVE`, `FALSE_POSITIVE`, `CONTROLLED_WORK`).
- Automatically exports forensic incident records to `data/incidents/{event_id}.json` and curates edge-cases into `data/active_learning/curated_queue.jsonl`.

### 5. Edge Perception & Kinematic Forecasting (`src/perception/`, `src/tracking/`)
- YOLOv8 hardware-accelerated detection with PPE compliance verification (Hardhat/Vest).
- Metric Planar Homography ($H^{-1}$) with real-time calibration drift monitoring ($RMSE < 0.15\text{m}$).
- GATv2 Spatio-Temporal Graph Attention Network predicting 3.0s future multi-agent trajectories.

---

## Repository Structure

```
SentinelZone-AI/
|-- config/
|   +-- default_config.json        # Calibrated homography, thresholds, and envelope specs
|-- data/
|   |-- active_learning/           # Curated active learning retraining queue (.jsonl)
|   |-- incidents/                 # Forensic incident dossiers (.json)
|   |-- manifests/                 # Compiled shift safety manifests (.json)
|   |-- real_videos/               # Real-world site videos (dynamic drop folder)
|   |-- test_videos/               # Calibrated scenario video sequences
|   +-- roboflow_downloaded/       # Roboflow validation and test splits
|-- src/
|   |-- api/
|   |   |-- app.py                 # FastAPI application with REST & WebSocket routes
|   |   +-- demo_service.py        # GPU processor, video scanner, and telemetry cache
|   |-- dashboard/
|   |   +-- index.html             # Three.js Digital Twin & AI Supervisor Copilot UI
|   |-- graph/                     # LangGraph Autonomous Agentic Modules
|   |   |-- __init__.py            # Module exports
|   |   |-- llm_provider.py        # Multi-provider factory with DeterministicAgentLLM
|   |   |-- context_pipeline.py    # Pipeline 1: PTW to BIM Dynamic Spatial Envelopes
|   |   |-- adjudication_pipeline.py# Pipeline 2: Multimodal Incident Adjudication
|   |   +-- supervisor_agent.py    # Pipeline 3: ReAct Supervisor Agent with 6 tools
|   |-- perception/
|   |   |-- detector.py            # YOLOv8 + PPE multi-class detector
|   |   +-- dataset_downloader.py  # Automated Roboflow dataset downloader
|   |-- spatial/
|   |   |-- homography.py          # Planar homography matrix & calibration engine
|   |   +-- bim_resolver.py        # BIM/IFC geodetic coordinate resolver
|   |-- tracking/
|   |   |-- ekf.py                 # Kinematic Extended Kalman Filter
|   |   |-- gnn_forecaster.py      # GATv2 Spatio-Temporal Graph Neural Network
|   |   +-- conflict_engine.py     # Dynamic exclusion envelopes & debouncer
|-- tests/
|   |-- test_agent_api.py          # REST API tests for autonomous agent endpoints
|   |-- test_demo_api.py           # Core gateway and telemetry tests
|   |-- test_detector.py           # YOLO and PPE tests
|   |-- test_ekf.py                # Kinematic tracking tests
|   |-- test_gnn_forecaster.py     # GATv2 trajectory forecast tests
|   |-- test_homography.py         # Homography projection and drift tests
|   |-- test_incident_agent.py     # Incident reporting tests
|   |-- test_pipelines.py          # Pipeline 1 & Pipeline 2 LangGraph tests
|   |-- test_safety_engine.py      # Safety envelopes and debouncing tests
|   +-- test_supervisor_agent.py   # Supervisor agent & 6 domain tools unit tests
|-- agent.md                       # Comprehensive mathematical derivations & formal specs
|-- pyproject.toml                 # Project metadata and dependencies
```

---

## REST API Specification

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/agent/supervisor/chat` | Multi-turn chat with the Autonomous Site Safety Supervisor ReAct agent |
| `POST` | `/api/v1/agent/pipeline1/compile_manifest` | Compiles raw PTW text into dynamic BIM spatial exclusion envelopes |
| `POST` | `/api/v1/agent/pipeline2/adjudicate` | Multimodal VLM forensic triage and active learning curation |
| `GET` | `/api/v1/agent/toolbox_talk/{site_id}` | Generates OSHA 29 CFR 1926 daily safety toolbox briefing |
| `GET` | `/api/v1/agent/active_learning/queue` | Returns curated edge-case dataset samples ready for model retraining |
| `GET` | `/api/v1/demo/scenarios` | Returns catalog of dynamically discovered site videos and scenarios |
| `GET` | `/api/v1/demo/telemetry/{scenario_id}` | Returns complete sequential EKF and near-miss telemetry |
| `GET` | `/health` | Hardware and service health check (CUDA device, active connections) |

---

## Quick Start

### 1. Build the standalone CPU image

The runtime image contains the FastAPI application, local browser assets, and three offline demo bundles. Camera credentials, SSH keys, model weights, and private setup data are not baked into the image.

```bash
docker build -f Dockerfile -t sentinelzone-ai:standalone .
docker run --rm --name sentinelzone \
  -p 127.0.0.1:18080:8000 \
  -v sentinel-data:/opt/sentinelzone/data \
  sentinelzone-ai:standalone
```

Open **`http://127.0.0.1:18080/`**. The demo works without a camera. Complete **Camera setup** later when the RTSP details, measured calibration, detector weights, and a compatible forecasting checkpoint are available.

For the supported hardened Compose recipe, use `compose.yaml` and optionally `compose.gpu.yaml` or `compose.ssh.yaml`; see [deploy/PRODUCTION.md](deploy/PRODUCTION.md).

### 2. Run locally for development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-runtime.txt
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

The local development server can be used for source inspection, but production should use the pinned Docker runtime.

### 3. Run the test suite

```bash
python scripts/run_tests.py
node tests/hub.test.cjs
```

The Python wrapper uses an isolated temporary data directory. The final image gate should be run with the same pinned dependencies as the container; a host interpreter is not a substitute for the image-level check.


---

## Dynamic Zero-Configuration Video Ingestion

1. Drop any construction site video (`.mp4`, `.avi`, `.mov`, `.mkv`, `.webm`) into:
   ```
   data/real_videos/
   ```
2. In the dashboard at `http://localhost:8000`, click the **⟳ Rescan** button (or restart the server).
3. The video is automatically indexed, processed on the GPU, and ready for live playback, 3D metric twin tracking, and supervisor agent analysis.

---

## Mathematical Specification & Theory

For complete mathematical proofs, kinematic formulations, planar homography matrices, and GATv2 spatio-temporal attention derivations, refer to:
[**agent.md**](agent.md)

---

## License

Apache 2.0

## Production camera deployment

For the CPU-first, single-camera Docker setup, optional NVIDIA build, credential configuration, calibration requirements, and known deployment limits, see [the production guide](deploy/PRODUCTION.md).

Run the regression suite without modifying repository incident data:

```bash
python scripts/run_tests.py
```

The original test-count, latency, and model-accuracy claims above describe the original demo documentation; they are not production measurements. Live deployment requires camera and model validation.
