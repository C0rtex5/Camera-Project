# Validation of the Docker/VPN hub — 2026-10-07

- Active Python suite: **115 passed**, one dependency deprecation warning (Starlette/AnyIO). Includes no-login API, RTSP credentials and redaction, camera isolation/reconnects, GPU inference fallback, incidents/reviews, calibration, disk errors, Compose identity and launcher failures. Previous single-camera contract tests are preserved in `tests/legacy/` outside active collection.
- CPU image built successfully with Python **3.12.15**, torch **2.5.1**, torchvision **0.20.1**, fixed application dependencies and local ordinary-Git `yolov8n.pt`. Build context is allowlisted; no local database, environment file, evidence or virtual environment is copied.
- Base and NVIDIA Compose configurations validated. Default bind is **127.0.0.1:1221**. CPU/GPU share project/service/container identity, with new pinned volume `camera-project-hub-data`. No operation touched `sentinelzone-state`.
- JavaScript and launcher syntax checked. Launcher tests exercise successful GPU startup, GPU probe/start failures with CPU fallback, and build failures without hiding the error.
- CPU image runtime tested with **networking disabled**, UID 10001, read-only filesystem, capabilities dropped and init. Bundled detector inference on a blank frame produced a valid empty result. Panel, local assets, no-login APIs, write-only camera credentials, incident evidence/review, container recreation and backup/restore passed using disposable volumes. No real camera credentials were used or copied.

## Environment limits

This development host refuses executable startup with `no-new-privileges:true` (`operation not permitted`), independently of the application. Full production-hardening smoke therefore remains pending. Functional offline smoke passed with `python3 scripts/docker_hub_smoke.py --compat-host-security`, which explicitly omits that option **for diagnosis only**. The production Compose keeps it enabled. Repeat `python3 scripts/docker_hub_smoke.py` without this option on the target Docker host.

The host has NVIDIA hardware, but its Docker NVIDIA hook fails with a CDI/runtime configuration error before application startup. Successful NVIDIA inference in the deployment image was not verified. GPU launcher/inference fallback scenarios were covered with test doubles; configure Toolkit/runtime and repeat the real GPU deployment on the target host.

Real RTSP video over the server VPN, storage-capacity sizing, performance benchmarks and the 72-hour pilot require the intended server and site cameras. No remote server installation or SSH access was performed. Readiness is expected to be degraded before cameras are commissioned; liveness is independent.

## Correções de 2026-10-08

A imagem Spark usa a base fixa NVIDIA PyTorch 25.09 (Python 3.12/CUDA 13). O manifesto no registro confirmou variantes linux/amd64 e linux/arm64. Compose efetivo: camera-project:spark, Dockerfile.spark, modo auto e reserva NVIDIA. A construção e inferência GB10 reais ainda exigem a DGX Spark; não foram executadas neste host x86.

Os testes de launcher cobriram ARM64 e x86 com sucesso GPU, falha no probe, falha no startup e fallback CPU. Os testes RTSP cobriram Basic/Digest, caracteres especiais, senha recusada, caminho inválido, permissões e uso de credenciais salvas sem retorná-las ou persistir dados do teste. Duas câmeras locais responderam RTSP 200. A rodada específica de RTSP/deploy passou com 19 testes; após a validação de URLs, 28 testes de API/RTSP passaram. Dois testes JavaScript confirmaram abertura sem WebGL e reutilização do buffer com descarte do bitmap.

A imagem CPU foi construída e o ensaio offline de painel, modelo, credenciais, evidências, recriação e restauração passou no modo diagnóstico, devido à limitação local de no-new-privileges. Essa proteção permanece no Compose de produção.

O teste visual prolongado no preview, o login da câmera do servidor e a inferência GPU na Spark permanecem pendentes de confirmação no ambiente de destino.
