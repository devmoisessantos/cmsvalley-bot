"""Views reutilizáveis (botões de confirmação, etc.)."""

from __future__ import annotations

import discord


class ConfirmView(discord.ui.View):
    """Dois botões: Confirmar e Cancelar.

    Só quem executou o comando original pode clicar.
    Depois do clique, self.value fica True ou False e a view para.
    """

    def __init__(self, autor_id: int, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.autor_id = autor_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Apenas quem executou o comando pode confirmar.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
    async def botao_confirmar(
        self, interaction: discord.Interaction, botao: discord.ui.Button
    ):
        self.value = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="✅ Confirmado. Aplicando…",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def botao_cancelar(
        self, interaction: discord.Interaction, botao: discord.ui.Button
    ):
        self.value = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="❌ Operação cancelada.",
            view=self,
        )
        self.stop()
