"""Cog — painel de punições e tarefas (ex.: verbal que expira em 3 dias)."""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import (
    commands,
    tasks,
)
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS_PUNICOES,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import (
    PainelPostado,
    Punicao,
)
from src.punicoes.punicoes_panel import PainelPunicoesLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_punicoes(bot: discord.Client):
    """Publica o painel uma única vez e grava sua mensagem como referência no banco.

    Verifica primeiro o registro persistido para que reinícios não criem painéis
    duplicados. Se o canal ou a guilda estiverem indisponíveis, apenas registra o
    problema e não cria uma referência inválida para uma mensagem inexistente.
    """
    async with async_session() as session:
        resultado_da_consulta = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "punicoes")
        )
        if resultado_da_consulta.scalar_one_or_none() is not None:
            return

        canal_id = (
            CANAIS.get("PAINEL_PUNICOES") or CANAIS.get("CANAL_ADVERTENCIAS") or 0
        )
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.warning("⚠️ Canal do painel de punições não configurado.")
            return

        guild = bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        mensagem = await canal.send(view=PainelPunicoesLayout(guild))
        session.add(
            PainelPostado(
                nome_painel="punicoes",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await session.commit()
        registrador.info(f"✅ Painel de Punições postado em #{canal.name}.")


async def _expirar_verbais_vencidas(bot: commands.Bot) -> int:
    """
    ADV VERBAL com expira_em no passado: marca inativa e tira o cargo.

    Retorna quantos registros foram baixados.
    """
    agora = datetime.now(timezone.utc)
    guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if guild is None:
        return 0

    ids_verbal = {
        cargo_id
        for nome, cargo_id in CARGOS_PUNICOES.items()
        if "verbal" in nome.lower()
    }
    if not ids_verbal:
        return 0

    async with async_session() as session:
        resultado = await session.execute(
            select(Punicao).where(
                Punicao.ativa.is_(True),
                Punicao.expira_em.is_not(None),
                Punicao.expira_em <= agora,
            )
        )
        vencidas = list(resultado.scalars().all())
        if not vencidas:
            return 0

        for row in vencidas:
            row.ativa = False
            row.removida_em = agora
            row.motivo_remocao = "Expiração automática (ADV VERBAL — 3 dias)"
            row.removida_por = bot.user.id if bot.user else None
        await session.commit()

    # Tira o cargo se não houver outra verbal ativa do mesmo cargo
    removidos = 0
    for row in vencidas:
        membro = guild.get_member(row.discord_id)
        if membro is None:
            continue
        if row.cargo_id not in ids_verbal:
            continue
        async with async_session() as session:
            ainda_ativa = await session.execute(
                select(Punicao).where(
                    Punicao.discord_id == row.discord_id,
                    Punicao.cargo_id == row.cargo_id,
                    Punicao.ativa.is_(True),
                )
            )
            if ainda_ativa.scalar_one_or_none() is not None:
                continue
        role = guild.get_role(row.cargo_id)
        if role is not None and role in membro.roles:
            try:
                await membro.remove_roles(
                    role,
                    reason="ADV VERBAL expirada automaticamente (3 dias)",
                )
                removidos += 1
            except discord.HTTPException as erro:
                registrador.warning(
                    "Falha ao remover verbal expirada de %s: %s",
                    row.discord_id,
                    erro,
                )
    return len(vencidas)


class PunicoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tarefa_expirar_verbais.start()

    def cog_unload(self):
        self.tarefa_expirar_verbais.cancel()

    @tasks.loop(minutes=30)
    async def tarefa_expirar_verbais(self):
        """A cada 30 min: baixa ADV VERBAL com prazo vencido."""
        try:
            quantidade = await _expirar_verbais_vencidas(self.bot)
            if quantidade:
                registrador.info(
                    "Expiração de verbal: %s registro(s) baixado(s).",
                    quantidade,
                )
        except Exception:
            registrador.exception("Erro na tarefa de expirar verbais")

    @tarefa_expirar_verbais.before_loop
    async def _antes_expirar_verbais(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Registra o cog de punições (painel + expiração de verbal)."""
    await bot.add_cog(PunicoesCog(bot))
