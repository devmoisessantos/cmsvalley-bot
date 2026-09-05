"""
Domínio do wipe de temporada.

Fluxo atual (sem expulsão e sem recriar canais):

1. /wipe backup        — snapshot Discord + backup do banco + esvaziar tabelas
2. /wipe limpar-cargos — remove cargos e prefixos (mantém diretoria + HP S・Valley)

Logs visuais vão para o canal LOGS_WIPE.
"""
