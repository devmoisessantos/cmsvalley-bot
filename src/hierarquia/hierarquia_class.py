# src/listeners/hierarquia_listener.py
import asyncio

import discord
from discord.ext import commands

from src.hierarquia.hierarquia_service import (
    atualizar_hierarquia,
    obter_cargo_mais_alto,
)


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
            print(f"✅ Hierarquia atualizada (cargos: {cargos_para_atualizar})")
        except asyncio.CancelledError:
            print("⏸️ Atualização adiada — nova mudança detectada, acumulando")
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HierarquiaListener(bot))
