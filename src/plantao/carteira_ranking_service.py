# src/plantao/carteira_ranking.py
"""Ranking de moedas em tempo real (edita a mesma mensagem no canal)."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import (
    PainelPostado,
    Usuario,
)
from src.plantao.carteira_service import (
    cotacao_moeda_do_membro,
    membro_elegivel_ranking_moedas,
    ranking_top_moedas,
    zerar_saldo_moedas,
)
from src.recrutamento.recrutamento_service import resolver_id_fivem
from src.utils.formatacao import formatar_dinheiro
from src.utils.relogio import timestamp_unix_agora

logger = logging.getLogger(__name__)

NOME_PAINEL_RANKING_MOEDAS = "ranking_moedas_tempo_real"


async def _buscar_registro() -> PainelPostado | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_RANKING_MOEDAS
            )
        )
        return resultado.scalar_one_or_none()


async def _salvar_registro(canal_id: int, message_id: int) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_RANKING_MOEDAS
            )
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            sessao.add(
                PainelPostado(
                    nome_painel=NOME_PAINEL_RANKING_MOEDAS,
                    canal_id=canal_id,
                    message_id=message_id,
                )
            )
        else:
            registro.canal_id = canal_id
            registro.message_id = message_id
        await sessao.commit()


async def _nicknames_do_banco(discord_ids: list[int]) -> dict[int, str]:
    """Busca nickname_atual em usuarios para quem não está no cache da guild."""
    if not discord_ids:
        return {}
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario.discord_id, Usuario.nickname_atual).where(
                Usuario.discord_id.in_(discord_ids),
                Usuario.nickname_atual.is_not(None),
            )
        )
        return {int(row[0]): str(row[1]) for row in resultado.all() if row[1]}


def _rotulo_membro(
    guilda: discord.Guild,
    discord_id: int,
    nick_banco: str | None,
) -> str:
    """
    Resolve como mostrar o membro no ranking.

    1) Membro na guild → mention (melhor UX)
    2) Nickname salvo no banco → nome formatado + mention oculta
    3) Fallback → mention por ID (<@id> o Discord ainda resolve se conhecer o user)
    """
    membro = guilda.get_member(discord_id)
    if membro is not None:
        return membro.mention

    # Fora do cache / saiu do servidor: evita ID cru
    if nick_banco:
        return f"**{nick_banco}** (<@{discord_id}>)"
    return f"<@{discord_id}>"


async def montar_view_ranking_moedas(guilda: discord.Guild) -> discord.ui.LayoutView:
    """Monta o card do ranking apenas com membros ainda elegíveis.

    Consulta os maiores saldos e remove do ranking quem saiu da organização,
    zerando seu saldo no banco para impedir que moedas de ex-membros continuem
    aparecendo ou possam ser usadas indevidamente. Devolve uma view pronta para
    publicar ou editar no Discord, inclusive quando não houver participantes.
    """
    # Cotação do cargo atual de cada membro na guild (para saldo legado
    # que ainda está congelado na mínima de R$10.000).
    cotacao_por_membro: dict[int, int] = {}
    for membro in guilda.members:
        if membro_elegivel_ranking_moedas(membro):
            cotacao_por_membro[membro.id] = cotacao_moeda_do_membro(membro)

    candidatos = await ranking_top_moedas(
        15,
        cotacao_por_membro=cotacao_por_membro,
    )

    # Filtra só quem ainda faz parte da organização; zera saldo de quem saiu/exonerado
    # candidatos: (discord_id, saldo_moedas, valor_ingame dos lotes)
    top: list[tuple[int, int, int]] = []
    for discord_id, saldo, valor_ingame in candidatos:
        membro = guilda.get_member(discord_id)
        if membro_elegivel_ranking_moedas(membro):
            top.append((discord_id, saldo, valor_ingame))
            if len(top) >= 15:
                break
        else:
            # Saiu do servidor / sem hierarquia / exonerado → limpa moedas
            try:
                await zerar_saldo_moedas(
                    discord_id,
                    motivo=(
                        "Removido do ranking: saiu do servidor ou não faz "
                        "mais parte da organização"
                    ),
                )
            except Exception as erro:
                logger.warning(
                    "Ranking moedas: falha ao zerar saldo de %s (%s)",
                    discord_id,
                    erro,
                )

    ids_fora_cache = [
        discord_id for discord_id, _, _ in top if guilda.get_member(discord_id) is None
    ]
    nicknames = await _nicknames_do_banco(ids_fora_cache)

    linhas_podio: list[str] = []
    linhas_resto: list[str] = []

    for posicao, (discord_id, saldo, valor_ingame) in enumerate(top, start=1):
        mencao = _rotulo_membro(guilda, discord_id, nicknames.get(discord_id))
        fid = await resolver_id_fivem(discord_id) or "—"
        equiv = formatar_dinheiro(valor_ingame)
        if posicao == 1:
            linhas_podio.append(
                f"`🥇` **1º** {mencao} · **FID:** `{fid}` · **{saldo}** · `{equiv}`"
            )
        elif posicao == 2:
            linhas_podio.append(
                f"`🥈` **2º** {mencao} · **FID:** `{fid}` · **{saldo}** · `{equiv}`"
            )
        elif posicao == 3:
            linhas_podio.append(
                f"`🥉` **3º** {mencao} · **FID:** `{fid}` · **{saldo}** · `{equiv}`"
            )
        else:
            linhas_resto.append(
                f"- **{posicao}º** {mencao} · FID `{fid}` · **{saldo}** · `{equiv}`"
            )

    if not linhas_podio and not linhas_resto:
        corpo_rank = "_Ninguém com saldo de moedas ainda._"
    else:
        partes_corpo: list[str] = []
        if linhas_podio:
            partes_corpo.append("\n".join(linhas_podio))
        if linhas_resto:
            if linhas_podio:
                partes_corpo.append("______")
            partes_corpo.append("\n".join(linhas_resto))
        corpo_rank = "\n".join(partes_corpo)

    momento = int(timestamp_unix_agora())
    url_icone = guilda.icon.url if guilda.icon else None

    texto_cabecalho = (
        "# 🏆 Ranking de Moedas — Top 15\n"
        "```yaml\n"
        "📌 Este ranking é atualizado automaticamente e considera apenas "
        "a quantidade de moedas em posse de cada jogador.\n"
        "```\n"
        "> **Classificação por quantidade de moedas (equiv. R$)** **·** "
        "**sem premiação** **·** **sem reset**"
    )

    rodape = (
        f"-# **{guilda.name}**  ·  **atualizado em tempo real**  ·  <t:{momento}:R>"
    )

    componentes: list = []
    if url_icone:
        componentes.append(
            discord.ui.Section(
                texto_cabecalho,
                accessory=discord.ui.Thumbnail(url_icone),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_cabecalho))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(f"## Ranking\n{corpo_rank}"))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.yellow())
    )
    return view


async def atualizar_ranking_moedas(bot: discord.Client) -> None:
    """
    Cria ou edita a mensagem única do ranking de moedas.

    Chamada a cada 1 minuto pelo loop: sempre reedita para o rodapé
    ``<t:R>`` voltar a "há 1 segundo". Um edit por minuto é seguro.
    """
    canal_id = CANAIS.get("RANKING_MOEDAS") or 0
    if not canal_id:
        return

    guilda = bot.get_guild(int(GUILD_ID))
    if guilda is None:
        return
    canal = guilda.get_channel(int(canal_id))
    if not isinstance(canal, discord.TextChannel):
        return

    view = await montar_view_ranking_moedas(guilda)
    registro = await _buscar_registro()

    if registro is not None:
        try:
            mensagem = await canal.fetch_message(int(registro.message_id))
            # Components V2: limpa content/embeds para o rodapé <t:R> entrar.
            await mensagem.edit(content=None, embeds=[], view=view)
            return
        except discord.NotFound as erro:
            logger.warning("Ranking moedas: mensagem sumiu, recriando (%s)", erro)
        except discord.HTTPException as erro:
            codigo = getattr(erro, "status", None)
            if codigo == 429:
                logger.warning(
                    "Ranking moedas: rate limit 429; tenta no próximo ciclo."
                )
                return
            logger.warning("Ranking moedas: recriando mensagem (%s)", erro)

    try:
        mensagem = await canal.send(view=view)
        await _salvar_registro(canal.id, mensagem.id)
    except discord.HTTPException as erro:
        codigo = getattr(erro, "status", None)
        if codigo == 429:
            logger.debug(
                "Ranking moedas: rate limit ao postar; tenta no próximo ciclo."
            )
            return
        logger.warning("Ranking moedas: falha ao postar (%s)", erro)
