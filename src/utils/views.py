# src/utils/views.py
"""
Views reutilizáveis (confirmação, etc.).

Nota: ConfirmView ainda usa discord.ui.View clássica porque o fluxo
de esperar o clique (wait/stop) e editar content= é simples assim.
Quando formos migrar confirmações para Components V2 de ponta a ponta,
este arquivo será o lugar.
"""

from __future__ import annotations

import discord

from src.utils.mensagens import responder_erro


class ConfirmView(discord.ui.View):
    """
    Dois botões: Confirmar e Cancelar.

    Só quem executou o comando original pode clicar.
    Depois do clique, self.value fica True ou False e a view para.
    """

    def __init__(self, autor_id: int, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.autor_id = autor_id
        self.value: bool | None = None

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        membro_que_clicou = interacao.user

        if membro_que_clicou.id != self.autor_id:
            await responder_erro(
                interacao,
                titulo="Ação não permitida",
                linhas=["Apenas quem executou o comando pode confirmar."],
            )
            return False

        return True

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
    async def botao_confirmar(
        self,
        interacao: discord.Interaction,
        botao: discord.ui.Button,
    ):
        self.value = True

        for item in self.children:
            item.disabled = True

        await interacao.response.edit_message(
            content="✅ Confirmado. Aplicando…",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def botao_cancelar(
        self,
        interacao: discord.Interaction,
        botao: discord.ui.Button,
    ):
        self.value = False

        for item in self.children:
            item.disabled = True

        await interacao.response.edit_message(
            content="❌ Operação cancelada.",
            view=self,
        )
        self.stop()
