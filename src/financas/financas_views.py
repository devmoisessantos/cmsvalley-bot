"""Views do canal de finanças (botão Pagamento realizado).

Usa discord.ui.View clássico de propósito:
- funciona com `content=` da mensagem (texto da solicitação)
- custom_id fixo sobrevive a restart do bot
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.plantao.permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.View):
    """
    Botão persistente. O texto da solicitação fica em message.content.
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)
        self.ja_pago = ja_pago

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
        self.add_item(botao_pagamento)

    async def _ao_marcar_pago(self, interacao: discord.Interaction):
        try:
            await self._processar_marcar_pago(interacao)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao marcar pagamento (finanças)",
                erro,
                contexto="ViewBotaoPagamentoFinancas._ao_marcar_pago",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao marcar pagamento. A equipe foi notificada."],
            )

    async def _processar_marcar_pago(self, interacao: discord.Interaction):
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
        except discord.HTTPException as erro_edit:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha HTTP ao editar solicitação de pagamento",
                erro_edit,
                contexto="edit_message pagamento realizado",
                usuario=membro,
            )
            if not interacao.response.is_done():
                await interacao.response.defer(ephemeral=True)
            if mensagem is not None:
                try:
                    await mensagem.edit(content=texto_novo, view=view_paga)
                except discord.HTTPException as erro_edit2:
                    await enviar_erro_para_log_erros(
                        interacao.guild,
                        "Falha HTTP ao editar mensagem de finanças",
                        erro_edit2,
                        contexto="message.edit pagamento realizado",
                        usuario=membro,
                    )
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
    """Registrar no startup (custom_id fixo)."""
    return ViewBotaoPagamentoFinancas(ja_pago=False)
