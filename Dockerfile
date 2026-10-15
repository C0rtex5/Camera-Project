FROM python:3.12.15-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SENTINEL_DATA_DIR=/var/lib/sentinel SENTINEL_WEIGHTS=/opt/sentinel/yolov8n.pt \
    SENTINEL_DEVICE=auto YOLO_CONFIG_DIR=/tmp/ultralytics MPLCONFIGDIR=/tmp/matplotlib \
    OPENCV_FFMPEG_LOGLEVEL=-8 OPENCV_LOG_LEVEL=SILENT
WORKDIR /opt/sentinel
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home sentinel \
    && mkdir -p /var/lib/sentinel /opt/sentinel/data/test_videos \
    && chown -R sentinel:sentinel /var/lib/sentinel /opt/sentinel
COPY requirements-hub.txt requirements-hub.lock ./
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.5.1
ARG TORCHVISION_VERSION=0.20.1
ARG SYMPY_VERSION=1.13.1
RUN pip install --no-cache-dir torch==${TORCH_VERSION} torchvision==${TORCHVISION_VERSION} --index-url ${TORCH_INDEX} \
    && python -c "import os; from pathlib import Path; Path('/tmp/backend.lock').write_text(Path('requirements-hub.lock').read_text().replace('sympy==1.13.1', 'sympy==' + os.environ['SYMPY_VERSION']))" \
    && pip install --no-cache-dir -r /tmp/backend.lock && pip check && rm /tmp/backend.lock
COPY --chown=sentinel:sentinel src ./src
COPY --chown=sentinel:sentinel config ./config
COPY --chown=sentinel:sentinel scripts ./scripts
COPY --chown=sentinel:sentinel yolov8n.pt ./yolov8n.pt
ARG OPENVINO=false
RUN if [ "$OPENVINO" = true ]; then \
      apt-get update && apt-get install -y --no-install-recommends ocl-icd-libopencl1 intel-opencl-icd && \
      rm -rf /var/lib/apt/lists/* && \
      pip install --no-cache-dir openvino==2025.0.0 openvino-telemetry==2024.5.0 && \
      mkdir -p /root/intel /home/sentinel/intel && \
      printf '0\n' > /root/intel/openvino_telemetry && \
      printf '0\n' > /home/sentinel/intel/openvino_telemetry && \
      python -c "from ultralytics import YOLO; from pathlib import Path; import hashlib; YOLO('yolov8n.pt').export(format='openvino', imgsz=640, device='cpu', half=False); Path('yolov8n_openvino_model/source.sha256').write_text(hashlib.sha256(Path('yolov8n.pt').read_bytes()).hexdigest())" && \
      chown -R sentinel:sentinel yolov8n_openvino_model; \
    fi
ENV CUDA_CACHE_PATH=/tmp/cuda-cache TRITON_CACHE_DIR=/tmp/triton-cache \
    MIOPEN_USER_DB_PATH=/tmp/miopen MIOPEN_CUSTOM_CACHE_DIR=/tmp/miopen \
    XDG_CACHE_HOME=/tmp/cache
USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=3)"
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
