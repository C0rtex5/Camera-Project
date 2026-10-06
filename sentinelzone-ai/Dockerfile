FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    YOLO_CONFIG_DIR=/tmp/ultralytics MPLCONFIGDIR=/tmp/matplotlib \
    OPENCV_FFMPEG_LOGLEVEL=-8 OPENCV_LOG_LEVEL=OFF \
    OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SENTINEL_CPU_THREADS=2 OMP_NUM_THREADS=2 \
    SENTINEL_SETUP_FILE=/opt/sentinelzone/data/setup/camera.json
WORKDIR /opt/sentinelzone
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements-runtime.lock ./
RUN pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-runtime.lock
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
COPY src ./src
COPY config ./config
# The state directories must exist in the image and be owned by the runtime user
# before USER. An empty named volume mounted at /state inherits the ownership of
# the image directory it covers, so without this the documented hardened
# deployment (read-only root, --user 10001, -v state:/state) has a root-owned
# state volume and the application can never persist anything.
RUN useradd --uid 10001 --create-home sentinel \
    && mkdir -p data/test_videos /state/setup /state/surveys /state/manifests \
    && chown -R sentinel:sentinel data /state
ENV SENTINEL_MODE=production
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
