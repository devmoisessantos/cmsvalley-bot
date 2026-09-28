# src/backup/__init__.py
"""
Pacote do sistema de backup do servidor Discord e do banco.

Tudo administrativo entra pelo grupo **/backup** (único ponto de entrada).

Módulos:
  backup_cogs                 — comandos /backup (+ recuperar) e tasks
  backup_gerenciador_service  — snapshot estrutural (cargos/canais/membros)
  restauracao_service         — restaura estrutura a partir do JSON local
  comparacao_service          — diff backup × estado atual
  retrato_de_membros_service  — snapshot vivo + rejoin
  banco_no_discord_service    — cofre ZIP no LOG_BACKUP + painel ephemeral
  sincronizacao_api_service   — export/import aditivo do Postgres
  backup_do_banco_service     — pg_dump / JSON local em disco
  recuperacao_logs_service    — parse de canais de LOG → banco
  backup_logger               — logs visuais (Components V2)

Comandos soltos `/recuperar` foram removidos: use `/backup recuperar …`.
"""
