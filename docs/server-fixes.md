# GPU automática, conexão RTSP e estabilidade do painel

Use `./scripts/start-hub.sh auto` para detectar o hardware do host Docker e selecionar NVIDIA CUDA (incluindo Spark/Blackwell), AMD ROCm ou Intel OpenVINO, conforme compatibilidade. Uma inferência real valida a GPU antes do início; em falha, o launcher usa uma imagem CPU independente. O modo `gpu` exige GPU e reporta falha. O botão do painel retenta dispositivos existentes, sem instalar drivers nem trocar a imagem.

Em **Configurar câmeras**, informe URL sem credenciais, usuário/senha separados e clique em **Testar conexão e login**. O teste parte do servidor e distingue login recusado, caminho incorreto e problemas de rede/VPN. Erros ao salvar agora aparecem dentro da janela. Use `SENTINEL_ORIGIN=auto` para acesso pela mesma origem do painel, por localhost ou IP do servidor.

O plano métrico começa em 2D; **Ativar plano 3D** habilita WebGL quando desejado. O vídeo reaproveita o buffer e ignora quadros repetidos. Abas ocultas pausam as atualizações. Para iniciar localmente, execute `./scripts/start-local.sh` com o terminal aberto, e acesse `http://localhost:8000`.

Os detalhes de instalação, backup e atualização estão em [DOCKER_VPN.md](../deploy/DOCKER_VPN.md). A inferência GB10 e o login das câmeras do servidor precisam ser confirmados na Spark. O teste local confirmou duas câmeras RTSP com resposta 200.
