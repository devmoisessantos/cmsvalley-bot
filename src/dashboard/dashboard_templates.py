"""
Templates de texto do painel de KPIs do dashboard.

O layout é fixo; as variáveis vêm do overview da API (que lê o banco do
bot). Assim o visual do card no Discord muda só quando os números mudam.
"""

from __future__ import annotations


def barra_de_progresso(pct: int, largura: int = 10) -> str:
    """Monta uma barra textual `████░░░░░░` a partir de 0–100."""
    valor = max(0, min(100, int(pct)))
    preenchidos = valor // (100 // largura) if largura else 0
    preenchidos = max(0, min(largura, preenchidos))
    return "█" * preenchidos + "░" * (largura - preenchidos)


def formatar_linha_de_meta(meta: dict) -> str:
    """
    Uma meta do overview vira uma linha legível.

    Variáveis esperadas em meta:
      label, value, target, pct, inverse (opcional)
    """
    rotulo = str(meta.get("label") or meta.get("id") or "Meta")
    valor = meta.get("value", "—")
    alvo = meta.get("target", "—")
    try:
        pct = int(meta.get("pct") or 0)
    except (TypeError, ValueError):
        pct = 0
    pct = max(0, min(100, pct))
    barra = barra_de_progresso(pct)
    return f"**{rotulo}**\n`{barra}` {pct}% · {valor} / {alvo}"


def montar_corpo_de_kpis(
    metas: list[dict],
    *,
    ao_vivo: int | None = None,
    periodo_rotulo: str = "mês",
) -> str:
    """
    Corpo do painel de KPIs a partir da lista de metas do overview.

    Se não houver metas, devolve texto explicando o vazio.
    """
    if not metas:
        return (
            "Nenhuma meta cadastrada no dashboard.\n"
            "Defina as metas no site (Produções, Plantão, Recrutamento, "
            "Resposta de ticket)."
        )

    blocos = [formatar_linha_de_meta(meta) for meta in metas]
    texto = "\n\n".join(blocos)

    if ao_vivo is not None:
        texto = (
            f"{texto}\n\n"
            f"-# Em plantão agora: **{ao_vivo}** · período: {periodo_rotulo}"
        )
    else:
        texto = f"{texto}\n\n-# Período: {periodo_rotulo}"

    return texto


def montar_titulo_de_kpis() -> str:
    """Título fixo do card de KPIs no Discord."""
    return "# Metas do hospital\nDashboard CMS Valley · números ao vivo do banco"


def montar_rodape_de_kpis(fonte: str = "cmsvalley-bot") -> str:
    """Rodapé curto do card."""
    return f"-# Atualizado via {fonte}"


def montar_corpo_de_status(bot_info: dict) -> str:
    """
    Texto do card de status técnico do bot (latência, cogs, versão).

    Variáveis esperadas em bot_info:
      online, latencia_ms, guilds, membros, cogs, versao, motivo
    """
    online = bool(bot_info.get("online"))
    estado = "online" if online else "offline"
    latencia = bot_info.get("latencia_ms")
    latencia_txt = f"{latencia} ms" if latencia is not None else "—"
    guilds = bot_info.get("guilds")
    membros = bot_info.get("membros")
    cogs = bot_info.get("cogs")
    versao = bot_info.get("versao") or "—"
    motivo = bot_info.get("motivo") or ""

    linhas = [
        f"**Estado:** {estado}",
        f"**Latência:** {latencia_txt}",
        f"**Servidores:** {guilds if guilds is not None else '—'}",
        f"**Membros alcançados:** {membros if membros is not None else '—'}",
        f"**Cogs carregados:** {cogs if cogs is not None else '—'}",
        f"**Versão:** {versao}",
    ]
    if motivo and not online:
        linhas.append(f"\n_{motivo}_")
    return "\n".join(linhas)
