"""
Log visual de tickets finalizados no canal LOG_TICKETS.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import CANAIS
from src.database.models import Ticket
from src.tickets.tickets_service import nome_usuario_discord
from src.utils.formatacao import para_horario_brasilia

DIAS_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _formatar_horario_extenso(data_hora: datetime | None) -> str:
    local = para_horario_brasilia(data_hora) if data_hora else None
    if local is None:
        return "—"
    dia_semana = DIAS_SEMANA[local.weekday()]
    mes = MESES[local.month - 1]
    return (
        f"{dia_semana}, {local.day} de {mes} de {local.year} {local.strftime('%H:%M')}"
    )


def _formatar_aberto_ha(aberto_em: datetime | None) -> str:
    if aberto_em is None:
        return "—"
    if aberto_em.tzinfo is None:
        aberto_em = aberto_em.replace(tzinfo=timezone.utc)
    agora = datetime.now(timezone.utc)
    delta = agora - aberto_em
    total_segundos = int(delta.total_seconds())
    if total_segundos < 60:
        return "há poucos segundos"
    if total_segundos < 3600:
        minutos = total_segundos // 60
        return f"há {minutos} minuto" + ("s" if minutos != 1 else "")
    if total_segundos < 86400:
        horas = total_segundos // 3600
        return f"há {horas} hora" + ("s" if horas != 1 else "")
    dias = total_segundos // 86400
    return f"há {dias} dia" + ("s" if dias != 1 else "")


def _montar_linha_botoes_transcript(ticket: Ticket) -> discord.ui.ActionRow:
    """
    Botões de transcript no log / DM.

    - Acessar: link se url_transcript existir; senão desativado
    - Senha: sempre desativado (só exibe a senha no rótulo)
    """
    linha = discord.ui.ActionRow()
    senha = ticket.senha_transcript or "—"
    url = (ticket.url_transcript or "").strip()

    if url.startswith("http://") or url.startswith("https://"):
        linha.add_item(
            discord.ui.Button(
                label="Acessar o Transcript",
                emoji="🔗",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )
    else:
        linha.add_item(
            discord.ui.Button(
                label="Acessar o Transcript",
                emoji="🔗",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )

    linha.add_item(
        discord.ui.Button(
            label=f"Senha: {senha}",
            emoji="🔓",
            style=discord.ButtonStyle.success,
            disabled=True,
        )
    )
    return linha


class LogTicketFinalizadoView(discord.ui.LayoutView):
    """Card de log enviado em LOG_TICKETS após finalizar."""

    def __init__(
        self,
        ticket: Ticket,
        staff: discord.Member,
        autor_mention: str,
        nome_canal: str,
        consideracoes: str | None,
        guilda: discord.Guild | None = None,
    ) -> None:
        super().__init__(timeout=None)

        staff_username = nome_usuario_discord(staff)
        autor_username = ticket.autor_nome or "—"
        senha = ticket.senha_transcript or "—"
        consideracoes_texto = consideracoes or "Atendimento Finalizado"
        horario = _formatar_horario_extenso(ticket.finalizado_em)
        aberto_ha = _formatar_aberto_ha(ticket.aberto_em)

        texto_info = (
            f"> ℹ️ **__Informações do Ticket__**\n"
            f"- **`👮` Responsável por Finalizar:** "
            f"{staff.mention} / `**{staff.id}**` / `{staff_username}`\n"
            f"- **`❓` Categoria:** `{ticket.categoria_rotulo}`\n"
            f"- **`⏰` Horário Finalizado:** `{horario}`\n\n"
            f"> 🗂️ **__Detalhes do Ticket__**\n"
            f"- **`📌` Canal:** `{nome_canal}`\n"
            f"- **`⏰` Aberto:** [`{aberto_ha}`]\n"
            f"- **`🔢` ID:** {ticket.id}\n"
            f"- **`🙋` Autor:** {autor_mention} "
            f"( `**{ticket.autor_discord_id}**` / `{autor_username}` )\n\n"
            f"> ✏️ **__Considerações Finais__**\n"
            f"# {consideracoes_texto}\n"
            f"> **🔐 __Senha para visualização do Transcript:__**\n"
            f"# - ||`{senha}`||"
        )

        componentes: list = [
            discord.ui.TextDisplay("# 🔐 Ticket Finalizado com Sucesso"),
        ]

        url_icone = None
        if guilda is not None and guilda.icon is not None:
            url_icone = guilda.icon.url

        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_info,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_info))

        componentes.append(_montar_linha_botoes_transcript(ticket))

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)


async def enviar_log_ticket_finalizado(
    bot: discord.Client,
    ticket: Ticket,
    staff: discord.Member,
    autor_mention: str,
    nome_canal: str,
    consideracoes: str | None,
) -> None:
    """Publica o log no canal LOG_TICKETS."""
    canal_id = CANAIS.get("LOG_TICKETS") or 0
    if not canal_id:
        print("⚠️ LOG_TICKETS não configurado.")
        return

    canal = bot.get_channel(int(canal_id))
    if canal is None:
        try:
            canal = await bot.fetch_channel(int(canal_id))
        except discord.HTTPException:
            canal = None

    if canal is None:
        print(f"⚠️ Canal LOG_TICKETS ({canal_id}) não encontrado.")
        return

    guilda = getattr(canal, "guild", None) or staff.guild

    view = LogTicketFinalizadoView(
        ticket=ticket,
        staff=staff,
        autor_mention=autor_mention,
        nome_canal=nome_canal,
        consideracoes=consideracoes,
        guilda=guilda,
    )
    try:
        await canal.send(view=view)
    except discord.HTTPException as erro:
        print(f"⚠️ Falha ao enviar log de ticket: {erro}")
