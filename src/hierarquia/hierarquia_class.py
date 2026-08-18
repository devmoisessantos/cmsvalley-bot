# src/listeners/hierarquia_listener.py
"""
Escuta mudancas de cargo e manda atualizar o quadro de hierarquia.

Sempre que alguem ganha ou perde cargo no servidor, o Discord avisa em
`on_member_update`. Este ouvinte compara os cargos de antes com os de agora e,
so quando houve diferenca de verdade, pede a atualizacao do quadro.

Essa comparacao e o ponto importante: sem ela, qualquer troca de apelido ou de
status faria o bot reescrever o quadro inteiro sem motivo.
"""

import asyncio
import logging

import discord
from discord.ext import commands

from src.hierarquia.hierarquia_service import (
    atualizar_hierarquia,
    obter_cargo_mais_alto,
)

registrador = logging.getLogger(__name__)


class HierarquiaListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task = None
        self._cargos_pendentes: set[int] = (
            set()
        )  # 👈 acumula cargos afetados durante a janela
        self.DELAY_SEGUNDOS = 5  # 5 segundos - você pode ajustar pra 10, 30, etc

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Reage a mudanca de cargo e agenda a atualizacao do quadro.

        O Discord chama esta funcao para QUALQUER mudanca no membro: apelido, status,
        avatar. Por isso ela faz duas conferencias antes de trabalhar. Primeiro compara
        os cargos de antes e de depois; se sao os mesmos, nao faz nada. Depois compara o
        cargo mais alto: se o membro ganhou um cargo que nao muda a posicao dele no
        quadro, o quadro nao precisa ser reescrito.

        Ela nao atualiza na hora. Guarda os cargos afetados e cancela o agendamento
        anterior, para que dez mudancas em sequencia virem uma unica atualizacao no
        final em vez de dez reescritas do quadro.
        """
        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}

        if before_role_ids == after_role_ids:
            return

        # Calcula o cargo-mais-alto antes e depois
        cargo_antes = obter_cargo_mais_alto(after.guild, before.roles)
        cargo_depois = obter_cargo_mais_alto(after.guild, after.roles)

        # Se o cargo-mais-alto não mudou, a hierarquia visível não é afetada — ignora
        if cargo_antes == cargo_depois:
            return

        # Acumula os cargos afetados (no máximo 2: o antigo e o novo)
        if cargo_antes is not None:
            self._cargos_pendentes.add(cargo_antes.id)
        if cargo_depois is not None:
            self._cargos_pendentes.add(cargo_depois.id)

        if self._task and not self._task.done():
            self._task.cancel()

        self._task = asyncio.create_task(self._atualizar_com_delay(after.guild))

    async def _atualizar_com_delay(self, guild: discord.Guild):
        try:
            await asyncio.sleep(self.DELAY_SEGUNDOS)
            cargos_para_atualizar = self._cargos_pendentes.copy()
            self._cargos_pendentes.clear()
            await atualizar_hierarquia(guild, somente_cargos=cargos_para_atualizar)
            registrador.info(
                f"✅ Hierarquia atualizada (cargos: {cargos_para_atualizar})"
            )
        except asyncio.CancelledError:
            registrador.info(
                "⏸️ Atualização adiada — nova mudança detectada, acumulando"
            )
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HierarquiaListener(bot))
