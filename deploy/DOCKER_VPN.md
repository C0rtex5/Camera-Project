# Deploy do hub pela VPN (Linux)

## Preparação

Docker Engine, Compose v2 ou posterior e Python 3 são necessários no servidor. A VPN do host deve alcançar as câmeras. O hub usa até quatro câmeras; a taxa de IA deve ser medida no hardware real. Sem autenticação do painel, permita acesso à porta somente pela rede privada da VPN.

```bash
git clone https://github.com/C0rtex5/Camera-Project.git
cd Camera-Project
# Até o PR ser integrado, selecione codex/docker-server-deploy.
git switch codex/docker-server-deploy
cp .env.production.example .env.production
```

Edite `.env.production`: `SENTINEL_BIND_ADDRESS=0.0.0.0` faz o serviço escutar em todas as interfaces IPv4 do servidor. O padrão `SENTINEL_ORIGIN=auto` aceita pedidos da mesma origem pela qual o próprio painel foi aberto, tanto em `http://localhost:1221` como pelo IP real do servidor na VPN. O endereço `0.0.0.0` é apenas o host de escuta. Para mudar a porta, ajuste `SENTINEL_PORT`. Um endereço explícito em `SENTINEL_ORIGIN` restringe o acesso àquela origem (protocolo, endereço e porta), sem caminho ou barra final; pedidos de outras origens continuam bloqueados. Não coloque credenciais das câmeras neste arquivo.

```bash
./scripts/start-hub.sh auto
```

O launcher valida a configuração e espera o healthcheck. Não execute o Compose antigo simultaneamente: mantenha projeto `sentinelzone`, serviço `sentinel` e container `sentinelzone`. O novo volume `camera-project-hub-data` contém configuração, credenciais RTSP, incidentes, evidências, revisões e auditoria. O volume `sentinelzone-state` permanece intacto; esta versão não migra dados antigos. Nunca use `down -v` para atualizar.

## GPU automática, CPU e acesso offline

Use `./scripts/start-hub.sh auto` no servidor. O launcher consulta a arquitetura e os dispositivos **do host do Docker**, incluindo quando o cliente usa um daemon remoto. Ele prepara uma imagem CPU independente, lê o inventário PCI/render em um container temporário sem rede e escolhe uma imagem compatível. Não depende de `nvidia-smi` instalado no computador cliente.

| Hardware compatível | Imagem selecionada | Requisito do host |
|---|---|---|
| NVIDIA Maxwell–Hopper x86, conforme suporte CUDA/PyTorch | `camera-project:gpu`, PyTorch 2.5.1/CUDA 12.4 | Driver compatível e NVIDIA Container Toolkit |
| NVIDIA Blackwell x86 ou NVIDIA ARM64 SBSA, incluindo DGX Spark | `camera-project:spark`, NVIDIA PyTorch 25.09/CUDA 13 | Driver compatível e NVIDIA Container Toolkit |
| AMD x86 suportada pelo ROCm 6.4 | `camera-project:rocm`, PyTorch 2.9.1/ROCm 6.4 | Driver amdgpu, `/dev/kfd` e render nodes disponíveis |
| Intel x86 suportada pelo OpenVINO e pelo runtime OpenCL empacotado | `camera-project:openvino`, OpenVINO 2025.0 | Driver i915/xe e render nodes disponíveis |
| Sem GPU compatível ou falha em modo automático | `camera-project:cpu` | Nenhum runtime GPU necessário |

São variantes da mesma aplicação, com o mesmo serviço, porta e volume. Uma imagem CPU já criada não ganha bibliotecas CUDA/ROCm/OpenVINO apenas ao mudar uma variável; execute o launcher para selecionar/recriar a variante correta. ARM64 sozinho não identifica uma Spark. Jetson não é ARM64 SBSA e exige uma imagem L4T própria; nesta entrega usa CPU quando possível.

NVIDIA é consultada dentro de um container com acesso GPU antes de construir a variante CUDA. O launcher testa a reserva NVIDIA tradicional e, se necessário, o CDI nativo (`nvidia.com/gpu=all`), usando apenas o caminho que realmente funcionar. CDI exige suporte/configuração do daemon e especificações de dispositivos válidas; ele é habilitado por padrão no Docker Engine 28.3 ou posterior. A capacidade de cálculo distingue Blackwell de gerações anteriores. AMD e Intel recebem apenas os render nodes do fabricante selecionado e seus grupos numéricos reais; AMD também recebe `/dev/kfd`. Não há execução privilegiada, montagem de diretórios de drivers do host nem alteração automática de drivers. A inferência inicial usa um container separado **sem o volume de dados**, evitando acesso às credenciais das câmeras.

O launcher só inicia uma variante GPU após inferência real do modelo nela. O runtime enumera dispositivos visíveis e tenta os candidatos em ordem, preferindo placa dedicada a gráficos integrados e, depois, maior memória disponível. Se um candidato falhar, tenta o próximo. Não há divisão de câmeras entre múltiplas GPUs nesta versão. Em máquinas com vários fabricantes, a prioridade do launcher é NVIDIA, AMD e Intel; é possível restringir a família abaixo.

Configuração recomendada em `.env.production`:

```dotenv
SENTINEL_DEVICE=auto
SENTINEL_ACCELERATOR=auto
SENTINEL_GPU_INDEX=auto
SENTINEL_ORIGIN=auto
```

`SENTINEL_ACCELERATOR` também aceita `nvidia`, `rocm`, `openvino` ou `cpu`; `cuda` é um alias de NVIDIA no launcher. `SENTINEL_GPU_INDEX` pode ser um índice numérico dos dispositivos **visíveis ao runtime da imagem escolhida**. Para Intel, o índice corresponde a `GPU.N`. `SENTINEL_DEVICE` aceita `auto`, `cpu`, `gpu`, `cuda`, `rocm` ou `openvino`. No modo automático, uma configuração explícita CPU é respeitada; o launcher força CPU em fallback.

`./scripts/start-hub.sh cpu` força CPU. `./scripts/start-hub.sh gpu` exige inferência GPU válida e reporta falha se ela não puder ser usada. `auto` reporta falhas de imagem, runtime, inferência ou startup e inicia a imagem CPU. O painel mostra fabricante/backend, nome do dispositivo e motivo de degradação. Uma falha GPU durante operação descarta o quadro, recarrega o modelo em CPU e reinicia os estados de tracking, mantendo captura e visualização. Retentar GPU requer o botão **Tentar GPU novamente**, reinício ou novo deploy; o botão só usa bibliotecas/dispositivos já disponíveis naquele container.

O build Intel converte o modelo local para OpenVINO, empacota pesos e metadados e registra seu hash de origem. A execução não faz conversão nem downloads. Um modelo `.pt` diferente precisa de exportação OpenVINO correspondente; não se usa silenciosamente o detector antigo. O processamento das caixas mantém a correção de resize e o mapeamento de classes por metadados. CPU, CUDA e ROCm usam o `.pt` local.

O primeiro build exige internet. Para operação offline, prepare as variantes necessárias antes (`docker save`/`docker load`). O inventário e os testes de startup não baixam pesos. Atualizações, backup e restauração continuam usando o mesmo volume; nunca use `down -v`. Para AMD/Intel, use o launcher: ele gera as permissões de dispositivos específicas daquele servidor. Um `docker compose up` apenas com o arquivo base seleciona CPU e não concede acesso GPU.

A detecção de um fabricante não garante suporte a todo modelo já produzido: arquitetura, driver, kernel e bibliotecas precisam ser compatíveis. Hardware sem suporte falha na validação e usa CPU no modo automático, com diagnóstico visível. Fontes: [PyTorch — versões e wheels CUDA/ROCm](https://pytorch.org/get-started/previous-versions/), [NVIDIA PyTorch 25.09](https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-25-09.html), [NVIDIA — CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html), [Docker — dispositivos CDI](https://docs.docker.com/reference/cli/docker/container/run/#device), [ROCm — dispositivos em Docker](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/how-to/docker.html), [OpenVINO — GPU Intel](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/gpu-device.html).

## Primeiro uso

Abra a origem configurada. Cadastre até quatro câmeras com endereço RTSP **sem usuário/senha na URL**, identidade/site, usuário, senha e **Guardar Login** marcado. Campos vazios preservam as credenciais existentes; desmarcar a opção com campos vazios remove-as. Senhas não são devolvidas pelas APIs. O SQLite tem permissão 0600 e precisa guardar as credenciais de forma recuperável para conectar às câmeras; proteja também o volume e backups.

Use **Testar conexão e login** antes de salvar. O teste parte do servidor, usa as credenciais informadas ou já salvas e distingue login recusado, URL/canal inválido, indisponibilidade de rede/VPN e timeout. Uma resposta RTSP aceita não confirma a decodificação do vídeo: confirme o estado online. Erros de salvamento aparecem dentro da janela; se houver erro de acesso ao painel, configure `SENTINEL_ORIGIN=auto` no servidor e recrie o container pelo launcher. Esse erro ocorre antes de consultar a câmera; não indica senha RTSP incorreta. Para restringir o painel a um endereço, mantenha uma origem explícita correspondente ao navegador.

Salvar uma câmera indisponível preserva sua configuração e as credenciais marcadas em **Guardar Login**; o hub tenta reconectar automaticamente. Reiniciar a aplicação local ou recriar o container com o mesmo volume mantém os dados. O botão **Testar conexão e login** usa os dados informados sem salvá-los. Confirme vídeo e idade do último frame, dispositivo, taxa de IA, incidentes e revisão. Sem câmera/calibração/modelo disponível, `/health/ready` retorna 503 com detalhes; `/health/live` continua 200 enquanto o serviço responde e é usado para liveness. A calibração precisa de referências levantadas no local; sem ela, estimativas métricas ficam indisponíveis. Faça piloto de 72 horas e validação RTSP no servidor com as câmeras reais.

Demo opcional: coloque gravações em `demo-library/` e use `docker compose --env-file .env.production -f compose.yaml -f compose.demo.yaml up -d`. Não há gravações obrigatórias na imagem principal. O demo antigo fica em `/demo/`.

## Atualização e rollback

Na atualização de uma instalação existente, troque o valor fixo antigo de `SENTINEL_ORIGIN` por `auto` em `.env.production` para habilitar acesso por localhost e pelo IP do servidor. Valores explícitos continuam restritivos; a atualização não os sobrescreve. Não altere o volume persistente.

Guarde o commit atual antes de atualizar:

```bash
git rev-parse HEAD > .deploy-previous
# Selecione um commit revisado da branch/main; não use mudanças locais sem revisão.
git fetch origin
git checkout <commit-aprovado>
./scripts/start-hub.sh auto
```

Antes de atualizar, faça backup abaixo. Para rollback: `git checkout <commit-anterior>` e `./scripts/start-hub.sh auto`. Se a versão anterior usar outro esquema de dados, restaure o backup em um volume novo; nunca sobrescreva o volume atual. Voltar à aplicação antiga exige seu Compose e o volume antigo, após parar o novo container. O histórico do destino fica preservado.

## Backup e restauração

Pare o hub para que banco e evidências representem o mesmo instante. Escolha um nome de backup novo; os exemplos usam `backup-20261007`:

```bash
docker compose --env-file .env.production stop sentinel
docker compose --env-file .env.production run --rm --no-deps sentinel python -m scripts.hub_admin backup /var/lib/sentinel/backup-20261007
mkdir -p backups
chmod 700 backups
docker cp sentinelzone:/var/lib/sentinel/backup-20261007 backups/
./scripts/start-hub.sh auto
```

Backups incluem banco, credenciais locais e evidências. Guarde uma cópia fora do servidor em armazenamento privado. Arquivos de credenciais externos, se usados via montagem adicional em `/run/secrets`, precisam de backup separado. Remova backups internos após confirmar a cópia para limitar o uso de disco. A retenção padrão de evidências é sete dias, com teto 2 GiB; ajuste no ambiente. Isso não limita o tamanho do banco nem dos backups. Monitore disco do host.

Restaure em **volume vazio** e com o hub parado. Exemplo de recuperação para um volume temporário:

```bash
docker compose --env-file .env.production stop sentinel
docker volume create camera-project-hub-restored
docker run --rm --network none -v camera-project-hub-restored:/var/lib/sentinel -v "$PWD/backups/backup-20261007:/backup:ro" camera-project:cpu python -m scripts.hub_admin restore /backup
```

Após verificar os dados, ajuste `volumes.hub-data.name` para o volume restaurado e inicie pelo launcher. Não remova o volume anterior até confirmar a recuperação. Faça ensaio periódico de restauração.

## Operação e validação

`docker compose --env-file .env.production ps`, `logs --tail 100 sentinel` e `/api/v1/cameras` mostram estado. Os logs Docker são limitados a três arquivos de 10 MiB; há reinício automático, execução como UID 10001, filesystem somente leitura e 45 segundos para encerramento. O Docker marca liveness como unhealthy mas não reinicia apenas por esse estado; monitore-o externamente.

Testes do hub: `python -m pytest tests/test_api.py tests/test_hub_runtime.py tests/test_agent_api.py tests/test_detector_reliability.py tests/test_deploy.py`. Ensaio Docker isolado, sem internet: `python3 scripts/docker_hub_smoke.py`. Ele usa containers/volumes descartáveis e verifica modelo, painel, APIs, persistência, credenciais e backup/restauração. Os testes antigos da versão de uma câmera estão preservados como referência e não representam a nova interface/API. GPU real e RTSP no servidor exigem hardware/câmeras de destino.

## Painel local

O plano abre em 2D para evitar alocação WebGL no carregamento. **Ativar plano 3D** habilita a visualização original sob demanda. Quadros repetidos não são decodificados novamente e o canvas não é recriado a cada quadro. Abas ocultas pausam a atualização do painel.

Para iniciar fora do Docker: instale as dependências no `.venv` e execute `./scripts/start-local.sh`, mantendo o terminal aberto. O serviço responde em `http://localhost:8000`. Uma falha do processo precisa ser acompanhada pelo terminal; o encerramento do servidor é diferente de uma falha de renderização do navegador.

## Atualizar usando o pacote Git entregue

Quando a publicação GitHub estiver indisponível, transfira `docker-server-deploy.bundle` ao servidor. No checkout existente de Camera-Project, sem alterações locais pendentes e após backup:

```bash
git fetch /caminho/docker-server-deploy.bundle codex/docker-server-deploy
git switch --detach FETCH_HEAD
./scripts/start-hub.sh gpu
```

O pacote depende do histórico existente de Camera-Project; não importa o histórico do repositório original. O arquivo `.env.production` e o volume persistente ficam no servidor. Use `auto` após confirmar o primeiro startup GPU. A branch ainda não foi publicada nem um novo PR aberto pela conexão sem escrita.
