"""Views do canal de finanças (botão Pagamento realizado).

Solicitações usam Components V2 (LayoutView + Container), no padrão do projeto.
O botão tem custom_id fixo para continuar funcionando após restart.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.plantao.plantao_permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"


def _montar_botao_pagamento(*, ja_pago: bool) -> discord.ui.Button:
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
    return botao


class ViewSolicitacaoFinancasCard(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card Components V2 de solicitação financeira (padrão CardView / LogContainer).

    - titulo + corpo em TextDisplay
    - rodapé: nome da guilda + timestamp
    - botão Pagamento realizado (persistente)
    """

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild | None,
        cor: discord.Color = discord.Color.dark_gold(),
        ja_pago: bool = False,
        pago_por_mencao: str | None = None,
    ):
        super().__init__(timeout=None)
        self.titulo = titulo
        self.corpo = corpo
        self.guild_ref = guild
        self.cor = cor
        self.ja_pago = ja_pago

        momento = int(datetime.now(timezone.utc).timestamp())
        nome_guilda = guild.name if guild is not None else "CENTRO MÉDICO SUL VALLEY"
        rodape = f"-# {nome_guilda} • <t:{momento}:f>"

        corpo_final = corpo
        if ja_pago:
            corpo_final = (
                corpo
                + "\n\n"
                + "-# ✅ **PAGAMENTO REALIZADO**"
                + (f" por {pago_por_mencao}" if pago_por_mencao else "")
                + f" · <t:{momento}:f>"
            )

        linha_botoes = discord.ui.ActionRow()
        botao = _montar_botao_pagamento(ja_pago=ja_pago)
        botao.callback = self._ao_marcar_pago
        linha_botoes.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# {titulo}"),
                discord.ui.TextDisplay(corpo_final),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_botoes,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(rodape),
                accent_color=discord.Color.green() if ja_pago else cor,
            )
        )

    async def _ao_marcar_pago(self, interacao: discord.Interaction):
        try:
            await self._processar_marcar_pago(interacao)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao marcar pagamento (finanças)",
                erro,
                contexto="ViewSolicitacaoFinancasCard._ao_marcar_pago",
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

        # Evita reprocessar se a view já veio como paga
        if self.ja_pago:
            await responder_aviso(
                interacao,
                titulo="Já pago",
                linhas=["Esta solicitação já foi marcada como paga."],
                delay=6,
            )
            return

        titulo = self.titulo or "🏥 PAGAMENTO"
        corpo = self.corpo or "_Solicitação processada._"
        guilda = interacao.guild or self.guild_ref

        view_paga = ViewSolicitacaoFinancasCard(
            titulo=titulo,
            corpo=corpo,
            guild=guilda,
            ja_pago=True,
            pago_por_mencao=membro.mention,
        )

        try:
            await editar_mensagem_original(
                interacao,
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
            if interacao.message is not None:
                try:
                    await interacao.message.edit(view=view_paga)
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


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.View):
    """
    View clássica mínima só para registrar o custom_id no startup.
    Mensagens novas usam ViewSolicitacaoFinancasCard (Components V2).
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)
        botao = _montar_botao_pagamento(ja_pago=ja_pago)
        botao.callback = self._ao_marcar_pago_legado
        self.add_item(botao)

    async def _ao_marcar_pago_legado(self, interacao: discord.Interaction):
        """
        Clique em mensagem antiga (content= + View clássica) ou
        após restart sem estado do card.
        """
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

        # Mensagem antiga em content: mantém texto e desativa botão
        if texto_atual:
            carimbo = int(datetime.now(timezone.utc).timestamp())
            texto_novo = (
                texto_atual
                + "\n\n"
                + f"-# ✅ **PAGAMENTO REALIZADO** por {membro.mention} · <t:{carimbo}:f>"
            )
            view_paga = ViewBotaoPagamentoFinancas(ja_pago=True)
            try:
                await editar_mensagem_original(
                    interacao,
                    view=view_paga,
                    texto=texto_novo,
                )
            except discord.HTTPException as erro:
                await enviar_erro_para_log_erros(
                    interacao.guild,
                    "Falha ao marcar pagamento (legado content)",
                    erro,
                    contexto="ViewBotaoPagamentoFinancas legado",
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
            return

        # Após restart em card V2: não temos o corpo na instância registrada
        view_paga = ViewSolicitacaoFinancasCard(
            titulo="🏥 PAGAMENTO",
            corpo="_Solicitação processada (estado recuperado após restart)._",
            guild=interacao.guild,
            ja_pago=True,
            pago_por_mencao=membro.mention,
        )
        try:
            # texto=None apaga o texto antigo: o card V2 agora traz tudo.
            await editar_mensagem_original(
                interacao,
                view=view_paga,
                texto=None,
            )
        except discord.HTTPException:
            if not interacao.response.is_done():
                await interacao.response.defer(ephemeral=True)
            if mensagem is not None:
                try:
                    await mensagem.edit(view=view_paga)
                except discord.HTTPException as erro:
                    await enviar_erro_para_log_erros(
                        interacao.guild,
                        "Falha ao marcar pagamento (card após restart)",
                        erro,
                        contexto="ViewBotaoPagamentoFinancas restart",
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
