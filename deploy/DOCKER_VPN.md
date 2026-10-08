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

Edite `.env.production`: `SENTINEL_BIND_ADDRESS` é o IP da interface VPN **do servidor**, nunca `0.0.0.0`. Exemplo: `100.80.0.10`; `SENTINEL_ORIGIN=http://100.80.0.10:1221`. A origem deve ser exatamente o endereço aberto no navegador, sem barra final. Para mudar a porta, ajuste `SENTINEL_PORT` e a origem. O endereço precisa existir no servidor antes do início. O padrão é localhost na porta 1221. Não coloque credenciais das câmeras neste arquivo.

```bash
./scripts/start-hub.sh auto
```

O launcher valida a configuração e espera o healthcheck. Não execute o Compose antigo simultaneamente: mantenha projeto `sentinelzone`, serviço `sentinel` e container `sentinelzone`. O novo volume `camera-project-hub-data` contém configuração, credenciais RTSP, incidentes, evidências, revisões e auditoria. O volume `sentinelzone-state` permanece intacto; esta versão não migra dados antigos. Nunca use `down -v` para atualizar.

## CPU, GPU e acesso offline

`./scripts/start-hub.sh cpu` força CPU. `auto` tenta NVIDIA se o driver estiver disponível; verifica inferência real do modelo em CUDA dentro do container e usa CPU se a inicialização GPU falhar. Falhas de construção são reportadas. `gpu` exige GPU e falha explicitamente se indisponível. A opção `SENTINEL_DEVICE` aceita `auto`, `cpu` ou `cuda`; em deploy CPU o launcher substitui por `cpu`. Após falha de inferência CUDA, a aplicação reinicia o modelo em CPU e mantém a captura. Retentar GPU requer ação do operador ou reinício.

Para GPU, instale drivers compatíveis e NVIDIA Container Toolkit no host, configure o runtime Docker e valide acesso ao dispositivo. CPU não depende disso. A imagem CUDA x86 usa PyTorch 2.5.1/cu124; a imagem CPU x86 usa Python 3.12.15 e dependências fixadas. A Spark usa o stack ARM64/CUDA 13/PyTorch da imagem NVIDIA abaixo. O primeiro build exige internet; depois a execução usa o modelo Git local e ativos locais sem downloads. Prepare a imagem antecipadamente se o servidor não tiver internet (`docker save`/`docker load`).

## DGX Spark / GB10

A Spark tem CPU ARM64 e GPU Blackwell. A imagem x86/cu124 anterior não atende esse hardware. O launcher consulta a arquitetura do Docker host e usa `Dockerfile.spark` / `compose.spark.yaml` em ARM64. A base fixa `nvcr.io/nvidia/pytorch:25.09-py3` fornece Python 3.12, CUDA 13 e PyTorch 2.9 NVIDIA com suporte à Spark. Torch, Torchvision e o stack numérico dessa base são preservados; os pacotes da aplicação ficam em um ambiente separado. Fonte: [NVIDIA PyTorch 25.09](https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-25-09.html) e [hardware Spark](https://docs.nvidia.com/dgx/dgx-spark/hardware.html).

Atualize os arquivos da aplicação mantendo `.env.production` e o volume, então execute na Spark:

```bash
./scripts/start-hub.sh gpu
```

Esse modo exige GPU e reporta falha em vez de ocultar o problema. Após confirmar CUDA, use `auto` para permitir fallback. No ARM64, o fallback CPU usa a mesma imagem Spark sem reserva de GPU. Se iniciar diretamente pelo Compose, é necessário incluir os dois overrides:

```bash
docker compose --env-file .env.production -f compose.yaml -f compose.gpu.yaml -f compose.spark.yaml up -d --build
```

O botão **Tentar GPU novamente** agora sai do modo CPU forçado e tenta inferência CUDA. Ele só consegue usar os dispositivos e bibliotecas já disponíveis no container; não troca a imagem nem instala CUDA. O painel informa quando está em uma imagem CPU ou quando o runtime NVIDIA não disponibilizou a GPU. Uma imagem CPU já em execução precisa ser substituída pelo deploy Spark.

A construção e inferência ARM64 reais precisam ser verificadas na Spark. Este computador x86 não valida execução GB10. O launcher verifica o modelo real antes de iniciar o serviço GPU.

## Primeiro uso

Abra a origem configurada. Cadastre até quatro câmeras com endereço RTSP **sem usuário/senha na URL**, identidade/site, usuário, senha e **Guardar Login** marcado. Campos vazios preservam as credenciais existentes; desmarcar a opção com campos vazios remove-as. Senhas não são devolvidas pelas APIs. O SQLite tem permissão 0600 e precisa guardar as credenciais de forma recuperável para conectar às câmeras; proteja também o volume e backups.

Use **Testar conexão e login** antes de salvar. O teste parte do servidor, usa as credenciais informadas ou já salvas e distingue login recusado, URL/canal inválido, indisponibilidade de rede/VPN e timeout. Uma resposta RTSP aceita não confirma a decodificação do vídeo: confirme o estado online. Erros de salvamento aparecem dentro da janela; se houver erro de origem, corrija `SENTINEL_ORIGIN` para o endereço exato aberto no navegador.

Confirme vídeo e idade do último frame, dispositivo, taxa de IA, incidentes e revisão. Sem câmera/calibração/modelo disponível, `/health/ready` retorna 503 com detalhes; `/health/live` continua 200 enquanto o serviço responde e é usado para liveness. A calibração precisa de referências levantadas no local; sem ela, estimativas métricas ficam indisponíveis. Faça piloto de 72 horas e validação RTSP no servidor com as câmeras reais.

Demo opcional: coloque gravações em `demo-library/` e use `docker compose --env-file .env.production -f compose.yaml -f compose.demo.yaml up -d`. Não há gravações obrigatórias na imagem principal. O demo antigo fica em `/demo/`.

## Atualização e rollback

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
