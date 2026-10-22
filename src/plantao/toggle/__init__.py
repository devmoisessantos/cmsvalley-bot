"""
Subdomínio toggle: lê a log de serviço do jogo e complementa o plantão.

A cidade posta quem entrou e quem saiu de serviço. Este pacote trata essas
mensagens (duplicatas, ruído) e:

- desliga o plantão quando a pessoa sai de serviço no jogo
- avisa por DM se ela entrou em serviço e não está em call em 5 minutos
"""
