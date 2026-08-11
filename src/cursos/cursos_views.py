"""Botões e selects do domínio de cursos."""

from __future__ import annotations

import discord

from src.config import (
    CANAIS,
    VALOR_MOEDA_INGAME,
)
from src.cursos.cursos_service import (
    consultar_saldo_moedas,
    debitar_moedas_curso,
    listar_cursos_ordenados,
    membro_tem_curso,
    moedas_necessarias_para_curso,
    obter_curso,
    registrar_solicitacao_curso,
    rotulo_curso,
    texto_resumo_pagamento,
)
from src.plantao.permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.formatacao import formatar_reais
from src.utils.mensagens import (
    COR_INFO,
    COR_SUCESSO,
    responder_aviso,
    responder_erro,
    responder_sucesso,
)
from src.utils.log_container import LogContainerView

CUSTOM_ID_SELECT_CURSO = "cursos:select_solicitar"
CUSTOM_ID_PAGAR_MOEDAS = "cursos:pagar_moedas:"
CUSTOM_ID_PAGAR_INGAME = "cursos:pagar_ingame:"
CUSTOM_ID_CANCELAR = "cursos:cancelar"


class PainelCursosLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente — solicitar curso."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild_ref = guild

        opcoes: list[discord.SelectOption] = []
        for chave, dados in listar_cursos_ordenados():
            valor = int(dados.get("valor_ingame") or 0)
            desc = (
                f"{dados.get('nivel', '—')} · {formatar_reais(valor)}"
                if valor > 0
                else f"{dados.get('nivel', '—')} · grátis / a combinar"
            )
            opcoes.append(
                discord.SelectOption(
                    label=dados["nome"][:100],
                    value=chave,
                    description=desc[:100],
                    emoji=dados.get("emoji") or None,
                )
            )

        # Discord limita Select a 25 opções
        opcoes = opcoes[:25]
        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Escolha o curso que deseja solicitar…",
            options=opcoes,
            custom_id=CUSTOM_ID_SELECT_CURSO,
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_escolher_curso
        linha.add_item(seletor)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 📚 Solicitar Curso\n"
                    "Escolha o curso no menu. Você verá o **valor** e poderá pagar "
                    "com **moedas de plantão** ou registrar pagamento **in-game**.\n"
                    "-# Curso concluído = cargo correspondente no Discord."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _ao_escolher_curso(self, interacao: discord.Interaction):
        try:
            valores = interacao.data.get("values") if interacao.data else None
            if not valores:
                await responder_erro(
                    interacao,
                    titulo="Seleção inválida",
                    linhas=["Nenhum curso selecionado."],
                )
                return
            chave = valores[0]
            await enviar_confirmacao_curso(interacao, chave)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao escolher curso",
                erro,
                contexto="PainelCursosLayout._ao_escolher_curso",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao abrir o curso. A equipe foi notificada."],
            )


class ConfirmacaoCursoView(LoggingViewMixin, discord.ui.LayoutView):
    """Card efêmero de confirmação de pagamento."""

    def __init__(self, chave_curso: str, solicitante_id: int):
        super().__init__(timeout=180)
        self.chave_curso = chave_curso
        self.solicitante_id = solicitante_id
        dados = obter_curso(chave_curso) or {}
        valor = int(dados.get("valor_ingame") or 0)
        moedas = moedas_necessarias_para_curso(chave_curso)

        linha = discord.ui.ActionRow()
        if valor > 0:
            botao_moedas = discord.ui.Button(
                label=f"Pagar com moedas ({moedas})",
                style=discord.ButtonStyle.success,
                emoji="🪙",
                custom_id=f"{CUSTOM_ID_PAGAR_MOEDAS}{chave_curso}",
            )
            botao_moedas.callback = self._ao_pagar_moedas
            linha.add_item(botao_moedas)

            botao_ingame = discord.ui.Button(
                label="Pagar in-game",
                style=discord.ButtonStyle.primary,
                emoji="💵",
                custom_id=f"{CUSTOM_ID_PAGAR_INGAME}{chave_curso}",
            )
            botao_ingame.callback = self._ao_pagar_ingame
            linha.add_item(botao_ingame)
        else:
            botao_gratis = discord.ui.Button(
                label="Registrar solicitação",
                style=discord.ButtonStyle.success,
                emoji="📋",
            )
            botao_gratis.callback = self._ao_gratuito
            linha.add_item(botao_gratis)

        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_ID_CANCELAR,
        )
        botao_cancelar.callback = self._ao_cancelar
        linha.add_item(botao_cancelar)

        texto_valor = (
            formatar_reais(valor) if valor > 0 else "Sem valor fixo / a combinar"
        )
        texto_moedas = (
            f"Equivale a **{moedas}** moeda(s) de plantão "
            f"(1 moeda = {formatar_reais(VALOR_MOEDA_INGAME)})."
            if valor > 0
            else "Este curso não debita moedas automaticamente."
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# Confirmar · {rotulo_curso(chave_curso)}\n"
                    f"• **Valor in-game:** {texto_valor}\n"
                    f"• {texto_moedas}\n"
                    f"• **Nível:** `{dados.get('nivel', '—')}`\n\n"
                    "O pagamento com moedas debita **agora**. "
                    "O instrutor ainda precisa **aplicar** o curso (liberar o cargo)."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.gold(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é seu pedido",
                linhas=["Só quem abriu a confirmação pode concluir o pagamento."],
            )
            return False
        return True

    async def _ao_pagar_moedas(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        try:
            await processar_pagamento_moedas(interacao, self.chave_curso)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro pagamento curso (moedas)",
                erro,
                contexto="ConfirmacaoCursoView._ao_pagar_moedas",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro no pagamento",
                linhas=["Não foi possível debitar. A equipe foi notificada."],
            )

    async def _ao_pagar_ingame(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        try:
            await processar_pagamento_ingame(interacao, self.chave_curso)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro pagamento curso (in-game)",
                erro,
                contexto="ConfirmacaoCursoView._ao_pagar_ingame",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro na solicitação",
                linhas=["Falha ao registrar. A equipe foi notificada."],
            )

    async def _ao_gratuito(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        try:
            await processar_pagamento_ingame(
                interacao, self.chave_curso, forma="GRATUITO"
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro solicitação curso gratuita",
                erro,
                contexto="ConfirmacaoCursoView._ao_gratuito",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro na solicitação",
                linhas=["Falha ao registrar. A equipe foi notificada."],
            )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Solicitação de curso cancelada."],
            delay=8,
        )


async def enviar_confirmacao_curso(
    interacao: discord.Interaction,
    chave: str,
) -> None:
    dados = obter_curso(chave)
    if dados is None:
        await responder_erro(
            interacao,
            titulo="Curso desconhecido",
            linhas=[f"Chave `{chave}` não está no catálogo."],
        )
        return

    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Apenas no servidor",
            linhas=["Use este painel dentro do Discord do hospital."],
        )
        return

    if membro_tem_curso(membro, chave):
        await responder_aviso(
            interacao,
            titulo="Curso já concluído",
            linhas=[
                f"Você já possui {rotulo_curso(chave)}.",
                "Não é necessário solicitar de novo.",
            ],
            delay=12,
        )
        return

    view = ConfirmacaoCursoView(chave, membro.id)
    await interacao.response.send_message(view=view, ephemeral=True)


async def processar_pagamento_moedas(
    interacao: discord.Interaction,
    chave: str,
) -> None:
    dados = obter_curso(chave)
    if dados is None:
        await responder_erro(
            interacao, titulo="Curso inválido", linhas=["Catálogo desatualizado."]
        )
        return

    membro = interacao.user
    assert isinstance(membro, discord.Member)
    moedas = moedas_necessarias_para_curso(chave)
    valor = int(dados.get("valor_ingame") or 0)

    ok, saldo, erro_txt = await debitar_moedas_curso(membro.id, moedas)
    if not ok:
        await responder_erro(
            interacao,
            titulo="Saldo insuficiente",
            linhas=[erro_txt, f"Valor do curso: {formatar_reais(valor)}."],
        )
        return

    registro = await registrar_solicitacao_curso(
        discord_id=membro.id,
        chave_curso=chave,
        forma_pagamento="MOEDAS",
        moedas_debitadas=moedas,
        valor_ingame=valor,
    )

    await _publicar_pedido_no_canal_cursos(
        interacao,
        membro=membro,
        chave=chave,
        registro_id=registro.id,
        forma="MOEDAS",
        moedas=moedas,
        saldo_restante=saldo,
    )

    await responder_sucesso(
        interacao,
        titulo="Pagamento registrado",
        linhas=texto_resumo_pagamento(chave, "MOEDAS", moedas, saldo),
        delay=20,
    )


async def processar_pagamento_ingame(
    interacao: discord.Interaction,
    chave: str,
    forma: str = "IN_GAME",
) -> None:
    dados = obter_curso(chave)
    if dados is None:
        await responder_erro(
            interacao, titulo="Curso inválido", linhas=["Catálogo desatualizado."]
        )
        return

    membro = interacao.user
    assert isinstance(membro, discord.Member)
    valor = int(dados.get("valor_ingame") or 0)

    registro = await registrar_solicitacao_curso(
        discord_id=membro.id,
        chave_curso=chave,
        forma_pagamento=forma,
        moedas_debitadas=0,
        valor_ingame=valor,
    )

    await _publicar_pedido_no_canal_cursos(
        interacao,
        membro=membro,
        chave=chave,
        registro_id=registro.id,
        forma=forma,
        moedas=0,
        saldo_restante=None,
    )

    await responder_sucesso(
        interacao,
        titulo="Solicitação registrada",
        linhas=texto_resumo_pagamento(chave, forma, 0),
        delay=20,
    )


async def _publicar_pedido_no_canal_cursos(
    interacao: discord.Interaction,
    *,
    membro: discord.Member,
    chave: str,
    registro_id: int,
    forma: str,
    moedas: int,
    saldo_restante: int | None,
) -> None:
    guilda = interacao.guild
    if guilda is None:
        return
    canal_id = CANAIS.get("CANAL_PAINEL_SOLICITAR_CURSOS") or CANAIS.get(
        "SOLICITAR_CURSO_RESGATE"
    )
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        await enviar_erro_para_log_erros(
            guilda,
            "Canal de cursos não encontrado ao publicar pedido",
            RuntimeError(f"canal_id={canal_id}"),
            contexto="_publicar_pedido_no_canal_cursos",
            usuario=membro,
        )
        return

    linhas = (
        f"👤 {membro.mention} (`{membro.id}`)\n"
        f"📋 Pedido `#{registro_id}`\n"
        + "\n".join(texto_resumo_pagamento(chave, forma, moedas, saldo_restante))
        + "\n-# Instrutor: aplique o curso e conceda o cargo correspondente."
    )
    try:
        await canal.send(
            view=LogContainerView(
                titulo=f"Pedido de curso · {rotulo_curso(chave)}",
                linhas=linhas,
                guild=guilda,
                cor=COR_SUCESSO if forma == "MOEDAS" else COR_INFO,
            )
        )
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar pedido de curso",
            erro,
            contexto="_publicar_pedido_no_canal_cursos.send",
            usuario=membro,
        )


def view_persistente_cursos() -> PainelCursosLayout:
    return PainelCursosLayout()
