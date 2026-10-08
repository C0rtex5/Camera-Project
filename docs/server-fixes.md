# Atualização Spark, conexão RTSP e estabilidade do painel

Na DGX Spark, use o launcher atualizado: `./scripts/start-hub.sh gpu`. Ele escolhe a imagem ARM64 NVIDIA CUDA 13 e verifica o modelo em GPU antes de iniciar. Depois de confirmar GPU, `auto` permite fallback CPU. Um container da imagem CPU antiga precisa ser substituído; o botão do painel não instala CUDA nem altera a imagem.

Em **Configurar câmeras**, informe URL sem credenciais, usuário/senha separados e clique em **Testar conexão e login**. O teste parte do servidor e distingue login recusado, caminho incorreto e problemas de rede/VPN. Erros ao salvar agora aparecem dentro da janela. Se houver erro de origem, ajuste `SENTINEL_ORIGIN` para a URL e a porta abertas no navegador.

O plano métrico começa em 2D; **Ativar plano 3D** habilita WebGL quando desejado. O vídeo reaproveita o buffer e ignora quadros repetidos. Abas ocultas pausam as atualizações. Para iniciar localmente, execute `./scripts/start-local.sh` com o terminal aberto, e acesse `http://localhost:8000`.

Os detalhes de instalação, backup e atualização estão em [DOCKER_VPN.md](../deploy/DOCKER_VPN.md). A inferência GB10 e o login das câmeras do servidor precisam ser confirmados na Spark. O teste local confirmou duas câmeras RTSP com resposta 200.
