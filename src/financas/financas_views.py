"""Views do canal de finanças (botão Pagamento realizado).

O texto da solicitação vai em `content` da mensagem.
A view só carrega o botão com custom_id fixo (funciona após restart).
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.plantao.permissoes import e_diretoria
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.LayoutView):
    """
    Botão persistente para marcar solicitação de pagamento como paga.
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)

        linha_botoes = discord.ui.ActionRow()
        botao_pagamento = discord.ui.Button(
            label="Pagamento realizado" if not ja_pago else "Pago ✓",
            style=(
                discord.ButtonStyle.success
                if not ja_pago
                else discord.ButtonStyle.secondary
            ),
            emoji="✅",
            custom_id=CUSTOM_ID_PAGAMENTO_REALIZADO,
            disabled=ja_pago,
        )
        botao_pagamento.callback = self._ao_marcar_pago
        linha_botoes.add_item(botao_pagamento)

        cor_container = discord.Color.green() if ja_pago else discord.Color.dark_gold()
        self.add_item(
            discord.ui.Container(
                linha_botoes,
                accent_color=cor_container,
            )
        )

    async def _ao_marcar_pago(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Diretoria+** pode marcar pagamento como realizado."],
            )
            return

        mensagem = interacao.message
        texto_atual = (mensagem.content or "").strip() if mensagem else ""

        if "PAGAMENTO REALIZADO" in texto_atual.upper():
            await responder_aviso(
                interacao,
                titulo="Já pago",
                linhas=["Esta solicitação já foi marcada como paga."],
                delay=6,
            )
            return

        carimbo = int(datetime.now(timezone.utc).timestamp())
        texto_novo = (
            texto_atual + "\n\n" if texto_atual else ""
        ) + f"-# ✅ **PAGAMENTO REALIZADO** por {membro.mention} · <t:{carimbo}:f>"

        view_paga = ViewBotaoPagamentoFinancas(ja_pago=True)

        try:
            await interacao.response.edit_message(
                content=texto_novo,
                view=view_paga,
            )
        except discord.HTTPException:
            if not interacao.response.is_done():
                await interacao.response.defer(ephemeral=True)
            if mensagem is not None:
                try:
                    await mensagem.edit(content=texto_novo, view=view_paga)
                except discord.HTTPException:
                    await responder_erro(
                        interacao,
                        titulo="Falha ao atualizar",
                        linhas=["Não foi possível marcar o pagamento nesta mensagem."],
                    )
                    return

        await responder_sucesso(
            interacao,
            titulo="Pagamento marcado",
            linhas=["Solicitação marcada como **pagamento realizado**."],
            delay=8,
        )


def view_persistente_financas() -> ViewBotaoPagamentoFinancas:
    """Instância só para registrar o custom_id no startup do bot."""
    return ViewBotaoPagamentoFinancas(ja_pago=False)
