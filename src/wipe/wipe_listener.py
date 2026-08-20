"""
Quando alguém da diretoria pendente entra de novo, restoura os cargos.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import commands
from sqlalchemy import select

from src.database.conexao import async_session
from src.database.models import DiretoriaPendenteWipe

registrador = logging.getLogger(__name__)


class WipeListener(commands.Cog):
    """Reaplica cargos de gestão salvos no wipe quando a pessoa volta."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member) -> None:
        """Se o membro está na fila de diretoria pendente, devolve os cargos."""
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(DiretoriaPendenteWipe).where(
                    DiretoriaPendenteWipe.discord_id == membro.id,
                    DiretoriaPendenteWipe.restaurado_em.is_(None),
                )
            )
            pendente = resultado.scalar_one_or_none()
            if pendente is None:
                return

            nomes = [
                nome.strip()
                for nome in (pendente.nomes_cargos or "").split("||")
                if nome.strip()
            ]
            cargos_por_nome = {cargo.name: cargo for cargo in membro.guild.roles}
            aplicados: list[str] = []
            for nome in nomes:
                cargo = cargos_por_nome.get(nome)
                if cargo is None:
                    continue
                try:
                    await membro.add_roles(
                        cargo,
                        reason=f"Wipe temporada {pendente.temporada} — diretoria",
                    )
                    aplicados.append(nome)
                except discord.HTTPException as erro:
                    registrador.warning(
                        "[wipe] join restaurar %s em %s: %s",
                        nome,
                        membro.id,
                        erro,
                    )

            pendente.restaurado_em = datetime.now(timezone.utc)
            await sessao.commit()
            registrador.info(
                "[wipe] diretoria restaurada no join de %s: %s",
                membro.id,
                aplicados,
            )


async def setup(bot: commands.Bot) -> None:
    """Registra o listener de restauração da diretoria."""
    await bot.add_cog(WipeListener(bot))
