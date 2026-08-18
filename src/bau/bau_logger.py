"""Cards de alerta e DM do domínio baú."""

from __future__ import annotations

import logging

import discord

from src.bau.bau_views import (
    ViewCasoBau,
    ViewDmDevolucao,
)
from src.config import CANAIS
from src.database.models import CasoBau
from src.utils.error_handling import ignorar_falha_cosmetica
from src.utils.log_container import LogContainerView

registrador = logging.getLogger(__name__)


async def publicar_alerta_caso(
    guild: discord.Guild,
    caso: CasoBau,
    *,
    limite_1: int,
    limite_2: int | None,
    atualizar_mensagem_id: int | None = None,
) -> discord.Message | None:
    """Publica ou atualiza o card que acompanha um caso de excesso.

    Localiza o canal configurado e monta a visualização com os limites usados
    na decisão. Quando recebe o identificador de uma mensagem existente, tenta
    editá-la para não duplicar alertas; caso ela não possa ser editada, envia
    um novo card ao Discord e mantém o processamento do caso funcional.
    """
    canal = guild.get_channel(CANAIS.get("CANAL_ALERTA_BAU") or 0)
    if canal is None:
        registrador.warning(
            "⚠️ [bau] CANAL_ALERTA_BAU não configurado (ID 0 ou inválido)"
        )
        return None

    view = ViewCasoBau.montar_layout_alerta(
        caso,
        guild=guild,
        limite_1=limite_1,
        limite_2=limite_2,
    )
    if atualizar_mensagem_id:
        try:
            mensagem_antiga = await canal.fetch_message(atualizar_mensagem_id)
            await mensagem_antiga.edit(view=view)
            return mensagem_antiga
        except discord.HTTPException as erro_em_publicar_alerta_caso:
            # Enfeite que falhou: publicar o alerta do caso no canal.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_publicar_alerta_caso,
                o_que_falhou="publicar o alerta do caso no canal",
            )
    return await canal.send(view=view)


async def enviar_dm_excesso(
    membro: discord.Member,
    caso: CasoBau,
) -> bool:
    """DM de excesso no baú + log em LOG_NOTIFICACOES_DM."""
    from src.utils.notificacao import enviar_dm_view

    view_dm = ViewDmDevolucao(caso_id=caso.id, guild_id=membro.guild.id)
    return await enviar_dm_view(
        membro,
        view_dm,
        titulo_log="Baú — excesso / devolução",
        linhas_resumo=[
            f"Caso `#{caso.id}`",
            f"Passaporte `{getattr(caso, 'id_fivem', '—')}`",
            f"Status `{getattr(caso, 'status', '—')}`",
        ],
        guilda=membro.guild,
    )


async def log_item_desconhecido(guild: discord.Guild, nome: str, id_fivem: str) -> None:
    """Registra no canal de erros um item que o monitor não reconheceu.

    O aviso preserva o texto original e o passaporte para que a configuração
    possa ser corrigida, em vez de o evento ser descartado silenciosamente.
    """
    canal = guild.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal is None:
        return
    view = LogContainerView(
        titulo="Baú — item desconhecido no log",
        linhas=f"- **ID FiveM:** `{id_fivem}`\n- **Item bruto:** `{nome}`",
        guild=guild,
        cor=discord.Color.yellow(),
    )
    await canal.send(view=view)


async def log_parse_falhou(guild: discord.Guild, conteudo: str) -> None:
    """Envia ao canal de erros uma amostra do log que não pôde ser interpretado.

    Limita a amostra a oitocentos caracteres para caber no card do Discord e
    permitir investigar mudanças no formato de origem sem expor conteúdo demais.
    """
    canal = guild.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal is None:
        return
    trecho = (conteudo or "")[:800]
    view = LogContainerView(
        titulo="Baú — log ilegível (parse falhou)",
        linhas=f"```\n{trecho}\n```",
        guild=guild,
        cor=discord.Color.red(),
    )
    await canal.send(view=view)


async def log_verbal_aplicada(
    guild: discord.Guild,
    *,
    caso: CasoBau,
    tipo: str,
) -> None:
    """Publica no canal de alertas o registro de uma verbal aplicada.

    Busca todos os itens associados ao caso para que a equipe entenda a razão
    da medida. A mensagem no Discord também indica se existe um membro ligado
    ao caso e que o botão de ocorrência passou a estar disponível.
    """
    from src.bau.bau_service import (
        formatar_bloco_itens_yaml,
        ler_itens_do_caso,
    )

    canal = guild.get_channel(CANAIS.get("CANAL_ALERTA_BAU") or 0)
    if canal is None:
        return
    mapa_itens = ler_itens_do_caso(caso)
    view = LogContainerView(
        titulo=f"Baú — {tipo} · prazo estourado",
        linhas=(
            f"- **Caso:** `#{caso.id}`\n"
            f"- **FiveM:** `{caso.id_fivem}`\n"
            f"- **Itens:**\n{formatar_bloco_itens_yaml(mapa_itens)}\n"
            f"- **Membro:** "
            + (f"<@{caso.discord_id}>" if caso.discord_id else "_sem discord_")
            + "\n- Botão **Ocorrência Valley** liberado no card."
        ),
        guild=guild,
        cor=discord.Color.red(),
    )
    await canal.send(view=view)
