"""
Domínio dashboard — ponte entre o cmsvalley-bot e o site cmsvalley-api.

Responsabilidades:
  - Heartbeat (bot online, latência, guilds, membros, cogs)
  - Snapshot real de cargos/canais/guild para a API
  - Consumo da fila de comandos do site (embed, anúncio, sync de metas)
  - Painel de KPIs no Discord (Components V2, template com variáveis)
  - Catálogo que documenta de onde cada métrica vem no banco do bot
"""
