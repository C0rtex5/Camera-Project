FROM python:3.12.14-slim-bookworm
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
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
RUN pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 --index-url ${TORCH_INDEX} \
    && pip install --no-cache-dir -r requirements-hub.lock
COPY --chown=sentinel:sentinel src ./src
COPY --chown=sentinel:sentinel config ./config
COPY --chown=sentinel:sentinel scripts ./scripts
COPY --chown=sentinel:sentinel yolov8n.pt ./yolov8n.pt
USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=3)"
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
