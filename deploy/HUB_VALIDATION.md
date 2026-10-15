# Validation of the Docker/VPN hub — 2026-10-07

- Active Python suite: **115 passed**, one dependency deprecation warning (Starlette/AnyIO). Includes no-login API, RTSP credentials and redaction, camera isolation/reconnects, GPU inference fallback, incidents/reviews, calibration, disk errors, Compose identity and launcher failures. Previous single-camera contract tests are preserved in `tests/legacy/` outside active collection.
- CPU image built successfully with Python **3.12.15**, torch **2.5.1**, torchvision **0.20.1**, fixed application dependencies and local ordinary-Git `yolov8n.pt`. Build context is allowlisted; no local database, environment file, evidence or virtual environment is copied.
- Base and NVIDIA Compose configurations validated. Default bind is **0.0.0.0:1221**. CPU/GPU share project/service/container identity, with new pinned volume `camera-project-hub-data`. No operation touched `sentinelzone-state`.
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


## Correção de origem de 2026-10-15

Origem automática aplicada às APIs de escrita e aos três WebSockets: variável ausente, vazia ou `auto` usa a origem da requisição; valores explícitos continuam restritivos. O host continua `0.0.0.0`. Instalações existentes com origem fixa devem trocar `SENTINEL_ORIGIN` para `auto` em seu ambiente e recriar o container.

Validação específica: 49 testes de origem/deploy/RTSP e 21 testes de API/câmera desconectada passaram (70 no total). Incluem localhost, IP/porta externa, HTTPS, origem fixa, rejeição externa, cabeçalho de escrita obrigatório, Basic/Digest, senha incorreta, câmera indisponível, credenciais sem retorno na API, salvamento offline, reconexão e reinício com o mesmo banco.

Imagem CPU reconstruída. Ensaio sem internet passou com containers/volumes descartáveis: modelo local, cadastro, teste RTSP, bloqueio externo, credenciais, evidências, recriação e backup/restauração. Outro container com porta realmente publicada validou cadastro e teste via localhost e IP desta máquina. Os ensaios omitiram `no-new-privileges` apenas no modo diagnóstico devido à limitação do host registrada acima; a proteção permanece no Compose. Nenhum volume de produção foi alterado. Acesso à câmera real e execução na DGX Spark do servidor ainda dependem do ambiente de destino.


## Seleção de GPU entre fabricantes — 2026-10-15

A seleção passa a usar o host do daemon Docker: inventário PCI/render em container temporário sem rede, arquitetura e preflight NVIDIA tradicional/CDI. NVIDIA x86 anterior a Blackwell usa CUDA 12.4; Blackwell e NVIDIA ARM64 SBSA usam a imagem NVIDIA CUDA 13. AMD x86 usa ROCm 6.4/PyTorch 2.9.1; Intel x86 usa OpenVINO 2025.0 com exportação local e verificação do hash dos pesos. O fallback do launcher usa uma imagem CPU independente em qualquer família. Bibliotecas/cache GPU respeitam o filesystem somente leitura.

Foram cobertos 108 casos Python de regressão em rodadas direcionadas, incluindo múltiplas GPUs, prioridade à dedicada, falha de dispositivo, modos obrigatório/automático, CPU durante operação, origem, credenciais, captura e armazenamento. Os sete testes de identidade Compose e dois testes JavaScript também passaram. Perfis Compose CPU, NVIDIA, CDI, Spark, AMD e Intel foram validados. O Dockerfile Spark passou em `docker build --check`; wheels CPU Torch/Torchvision para Python 3.12/ARM64 foram confirmados no índice oficial.

Imagem CPU construída com `pip check` sem conflitos. Smoke sem internet em containers/volumes descartáveis passou para painel, APIs, modelo local, credenciais, incidentes, evidências, recriação e restauração. A imagem Intel foi construída e executou de fato o modelo OpenVINO em CPU sem internet; opt-out da telemetria e correspondência do modelo foram verificados. A imagem NVIDIA CUDA foi construída sem conflitos e executou inferência real com fallback CPU sem exposição GPU. A imagem AMD ROCm 6.4/PyTorch 2.9.1 também foi construída sem conflitos; importou as bibliotecas ROCm e executou inferência real em CPU sem GPU AMD exposta e com rede bloqueada. As quatro variantes x86 (CPU, CUDA, ROCm e OpenVINO) foram construídas. A execução GPU de cada família e a construção ARM64 na Spark ainda dependem de hardware/runtime de destino.

O inventário real no Docker identificou a GPU NVIDIA deste computador e o grupo de render. A reserva NVIDIA tradicional falhou devido ao runtime hook configurado em modo CDI; CDI nativo também falhou por especificações ausentes. Nenhuma alteração foi feita na configuração do host. A inferência GPU real ainda requer NVIDIA Container Toolkit/CDI funcional ou hardware AMD/Intel compatível. Os testes funcionais Docker omitiram `no-new-privileges` em diagnóstico devido à limitação local previamente documentada; a configuração de produção mantém essa proteção. Nenhum volume real de câmeras foi alterado.
