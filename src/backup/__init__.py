# src/backup/__init__.py
"""
Pacote do sistema de backup do servidor Discord.

Módulos:
  backup_manager   — serializa e grava snapshots JSON
  restore_manager  — restaura cargos/canais/membros com segurança
  diff_engine      — compara backup × estado atual
  member_snapshot  — snapshot vivo + rejoin automático
  backup_logger    — logs no canal configurado (Components V2)
  backup_cogs      — comandos /backup e listeners
"""
