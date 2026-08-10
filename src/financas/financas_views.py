"""Views do canal de finanças (botão Pagamento realizado)."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.plantao.permissoes import e_diretoria
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.LayoutView):
    """
    Só o botão (o texto da solicitação vai em `content` da mensagem).
    Assim o custom_id fixo funciona após restart e o texto original permanece.
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
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
        botao.callback = self._ao_marcar_pago
        linha.add_item(botao)

        cor = discord.Color.green() if ja_pago else discord.Color.dark_gold()
        self.add_item(
            discord.ui.Container(
                linha,
                accent_color=cor,
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
            await responder_aviso_ou_sucesso_ja_pago(interacao)
            return

        carimbo = int(datetime.now(timezone.utc).timestamp())
        texto_novo = (
            texto_atual + "\n\n" if texto_atual else ""
        ) + f"-# ✅ **PAGAMENTO REALIZADO** por {membro.mention} · <t:{carimbo}:f>"

        view_paga = ViewBotaoPagamentoFinancas(ja_pago=True)
        try:
            await interacao.response.edit_message(content=texto_novo, view=view_paga)
        except discord.HTTPException:
            try:
                await interacao.response.defer()
            except discord.HTTPException:
                pass
            if mensagem is not None:
                try:
                    await mensagem.edit(content=texto_novo, view=view_paga)
                except discord.HTTPException:
                    pass

        await responder_sucesso(
            interacao,
            titulo="Pagamento marcado",
            linhas=["Solicitação marcada como **pagamento realizado**."],
            delay=8,
        )


async def responder_aviso_ou_sucesso_ja_pago(interacao: discord.Interaction):
    from src.utils.mensagens import responder_aviso

    await responder_aviso(
        interacao,
        titulo="Já pago",
        linhas=["Esta solicitação já foi marcada como paga."],
        delay=6,
    )


def view_persistente_financas() -> ViewBotaoPagamentoFinancas:
    """Registrar no startup (custom_id fixo)."""
    return ViewBotaoPagamentoFinancas(ja_pago=False)


# Alias legado
ViewSolicitacaoFinancas = ViewBotaoPagamentoFinancas
"""Views do canal de finanças (botão Pagamento realizado)."""

from __future__ import annotations

import discord

from src.utils.error_handling import LoggingViewMixin

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.LayoutView):
    """
    Só o botão (o texto da solicitação vai em `content` da mensagem).
    Assim o custom_id fixo funciona após restart e o texto original permanece.
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
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
        botao.callback = self._ao_marcar_pago
        linha.add_item(botao)

        cor = discord.Color.green() if ja_pago else discord.Color.dark_gold()
        self.add_item(
            discord.ui.Container(
                linha,
                accent_color=cor,
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
            await responder_aviso_ou_sucesso_ja_pago(interacao)
            return

        carimbo = int(datetime.now(timezone.utc).timestamp())
        texto_novo = (
            texto_atual + "\n\n" if texto_atual else ""
        ) + f"-# ✅ **PAGAMENTO REALIZADO** por {membro.mention} · <t:{carimbo}:f>"

        view_paga = ViewBotaoPagamentoFinancas(ja_pago=True)
        try:
            await interacao.response.edit_message(content=texto_novo, view=view_paga)
        except discord.HTTPException:
            try:
                await interacao.response.defer()
            except discord.HTTPException:
                pass
            if mensagem is not None:
                try:
                    await mensagem.edit(content=texto_novo, view=view_paga)
                except discord.HTTPException:
                    pass

        await responder_sucesso(
            interacao,
            titulo="Pagamento marcado",
            linhas=["Solicitação marcada como **pagamento realizado**."],
            delay=8,
        )


async def responder_aviso_ou_sucesso_ja_pago(interacao: discord.Interaction):
    from src.utils.mensagens import responder_aviso

    await responder_aviso(
        interacao,
        titulo="Já pago",
        linhas=["Esta solicitação já foi marcada como paga."],
        delay=6,
    )


def view_persistente_financas() -> ViewBotaoPagamentoFinancas:
    """Registrar no startup (custom_id fixo)."""
    return ViewBotaoPagamentoFinancas(ja_pago=False)


# Alias legado
ViewSolicitacaoFinancas = ViewBotaoPagamentoFinancas
