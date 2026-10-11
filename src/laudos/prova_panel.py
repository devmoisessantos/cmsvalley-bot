"""
Painel do paciente e cards das perguntas da prova de porte.

- Painel fixo em CANAL_AVALIACAO_PSICOLOGICA (Iniciar avaliação).
- Cada pergunta usa botões A/B/C; após a escolha o card avança.
- Após a 5ª pergunta, um segundo card cobre as questões 6–10.
"""

from __future__ import annotations

import logging

import discord

from src.laudos.perguntas_porte import (
    TAMANHO_BLOCO_PORTE,
    TOTAL_PERGUNTAS_PORTE,
    obter_pergunta,
)
from src.laudos.prova_service import (
    barra_progresso_porte,
    finalizar_prova_paciente,
    gravar_resposta_prova,
    iniciar_prova_paciente,
    publicar_resultado_prova,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_erro,
    responder_view,
)

registrador = logging.getLogger(__name__)


class PainelProvaLaudoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente onde o paciente inicia a prova liberada."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild = guild

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []
        texto_cabecalho = (
            "# 🧠 Avaliação Psicológica — Prova de Porte\n"
            "### Prova teórica obrigatória para laudo de porte de arma\n"
            "-# Só quem tem consulta **liberada** pelo psicólogo pode iniciar."
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_cabecalho,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_cabecalho))

        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )
        componentes.append(
            discord.ui.TextDisplay(
                "## Regras\n"
                f"- **{TOTAL_PERGUNTAS_PORTE} questões** de múltipla escolha "
                f"(2 blocos de {TAMANHO_BLOCO_PORTE})\n"
                "- **Uma única tentativa** por consulta\n"
                "- Não feche a janela no meio da prova\n"
                "- Feedback e gabarito **somente para a equipe** no final\n"
                f"- Critério: **7/{TOTAL_PERGUNTAS_PORTE}** acertos na prova "
                "(o laudo final continua a cargo do psicólogo)"
            )
        )
        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Iniciar avaliação",
            style=discord.ButtonStyle.success,
            emoji="📝",
            custom_id="laudos:iniciar_prova_porte",
        )
        botao.callback = self._ao_iniciar
        linha.add_item(botao)
        componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_iniciar(self, interacao: discord.Interaction):
        """Valida liberação e mostra a primeira pergunta em mensagem efêmera."""
        try:
            ok, mensagem, consulta = await iniciar_prova_paciente(
                interacao.user.id
            )
            if not ok or consulta is None:
                await responder_erro(
                    interacao,
                    titulo="Não foi possível iniciar",
                    linhas=[mensagem],
                )
                return

            view_pergunta = montar_view_pergunta_porte(
                numero=1,
                consulta_id=consulta.id,
                guild=interacao.guild,
            )
            await responder_view(
                interacao,
                view_pergunta,
                ephemeral=True,
            )
        except discord.NotFound:
            return
        except Exception as erro:
            registrador.exception(
                "[laudos] falha ao iniciar prova de porte: %s", erro
            )
            await responder_erro(
                interacao,
                titulo="Erro ao iniciar",
                linhas=["Não consegui abrir a prova agora. Tente de novo."],
            )


def montar_view_pergunta_porte(
    *,
    numero: int,
    consulta_id: int,
    guild: discord.Guild | None,
) -> "PerguntaPorteLayoutView":
    """Monta o card de uma pergunta da prova de porte."""
    return PerguntaPorteLayoutView(
        numero=numero,
        consulta_id=consulta_id,
        guild=guild,
    )


class PerguntaPorteLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """Uma pergunta com botões A, B e C."""

    def __init__(
        self,
        *,
        numero: int,
        consulta_id: int,
        guild: discord.Guild | None,
    ):
        super().__init__(timeout=None)
        self.numero = numero
        self.consulta_id = consulta_id
        self.guild = guild

        pergunta = obter_pergunta(numero)
        if pergunta is None:
            self.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay("Pergunta não encontrada."),
                    accent_color=discord.Color.red(),
                )
            )
            return

        total_no_bloco = TAMANHO_BLOCO_PORTE
        if numero <= TAMANHO_BLOCO_PORTE:
            indice_no_bloco = numero
            titulo_bloco = (
                f"Bloco 1 · Questão {indice_no_bloco}/{total_no_bloco}"
            )
        else:
            indice_no_bloco = numero - TAMANHO_BLOCO_PORTE
            titulo_bloco = (
                f"Bloco 2 · Questão {indice_no_bloco}/{total_no_bloco}"
            )

        linha_botoes = discord.ui.ActionRow()
        for letra, texto in (
            ("A", pergunta.opcao_a),
            ("B", pergunta.opcao_b),
            ("C", pergunta.opcao_c),
        ):
            botao = discord.ui.Button(
                label=letra,
                style=discord.ButtonStyle.primary,
                custom_id=(
                    f"laudos:prova:{consulta_id}:{numero}:{letra}"
                ),
            )
            botao.callback = self._fazer_callback(letra)
            linha_botoes.add_item(botao)

        enunciado = (
            f"**{titulo_bloco}**\n\n"
            f"{pergunta.enunciado}\n\n"
            f"**A)** {pergunta.opcao_a}\n"
            f"**B)** {pergunta.opcao_b}\n"
            f"**C)** {pergunta.opcao_c}"
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# 📝 Prova de Porte — {numero}/"
                    f"{TOTAL_PERGUNTAS_PORTE}"
                ),
                discord.ui.TextDisplay(
                    f"-# {barra_progresso_porte(numero, TOTAL_PERGUNTAS_PORTE)}"
                ),
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small
                ),
                discord.ui.TextDisplay(enunciado),
                linha_botoes,
                accent_color=discord.Color.blurple(),
            )
        )

    def _fazer_callback(self, letra: str):
        async def _ao_escolher(interacao: discord.Interaction):
            await self._processar_resposta(interacao, letra)

        return _ao_escolher

    async def _processar_resposta(
        self,
        interacao: discord.Interaction,
        letra: str,
    ):
        """Grava a escolha e avança o card (ou abre o segundo bloco)."""
        ok, mensagem, _correta = await gravar_resposta_prova(
            consulta_id=self.consulta_id,
            discord_id_paciente=interacao.user.id,
            numero_pergunta=self.numero,
            letra_escolhida=letra,
        )
        if not ok:
            await responder_erro(
                interacao,
                titulo="Não registrei a resposta",
                linhas=[mensagem],
            )
            return

        if self.numero == TAMANHO_BLOCO_PORTE:
            # Fim do bloco 1: este card vira aviso; o bloco 2 vem em mensagem nova
            view_transicao = BlocoConcluidoLayoutView(
                bloco=1,
                letra_escolhida=letra,
                numero=self.numero,
            )
            await editar_mensagem_original(interacao, view=view_transicao)
            view_proxima = montar_view_pergunta_porte(
                numero=self.numero + 1,
                consulta_id=self.consulta_id,
                guild=interacao.guild,
            )
            await interacao.followup.send(
                view=view_proxima,
                ephemeral=True,
            )
            return

        if self.numero >= TOTAL_PERGUNTAS_PORTE:
            await self._concluir_prova(interacao)
            return

        view_proxima = montar_view_pergunta_porte(
            numero=self.numero + 1,
            consulta_id=self.consulta_id,
            guild=interacao.guild,
        )
        await editar_mensagem_original(interacao, view=view_proxima)

    async def _concluir_prova(self, interacao: discord.Interaction):
        """Fecha a prova, publica o resultado e avisa o paciente."""
        from src.database.conexao import async_session
        from src.database.models import ConsultaLaudo

        ok, mensagem, acertos, parecer = await finalizar_prova_paciente(
            consulta_id=self.consulta_id,
            discord_id_paciente=interacao.user.id,
        )
        if not ok:
            await responder_erro(
                interacao,
                titulo="Não finalizei a prova",
                linhas=[mensagem],
            )
            return

        guilda = interacao.guild
        if guilda is not None:
            async with async_session() as sessao:
                consulta = await sessao.get(ConsultaLaudo, self.consulta_id)
            if consulta is not None:
                try:
                    await publicar_resultado_prova(
                        guild=guilda,
                        consulta=consulta,
                        acertos=acertos,
                        parecer_sugerido=parecer,
                    )
                except Exception as erro_pub:
                    registrador.exception(
                        "[laudos] falha ao publicar resultado da prova: %s",
                        erro_pub,
                    )

        view_fim = ProvaEnviadaLayoutView(acertos=acertos, parecer=parecer)
        await editar_mensagem_original(interacao, view=view_fim)


class PerguntaRespondidaLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """Card curto após a escolha: botões desabilitados e letra marcada."""

    def __init__(
        self,
        *,
        numero: int,
        consulta_id: int,
        letra_escolhida: str,
    ):
        super().__init__(timeout=None)
        linha = discord.ui.ActionRow()
        for letra in ("A", "B", "C"):
            estilo = (
                discord.ButtonStyle.success
                if letra == letra_escolhida
                else discord.ButtonStyle.secondary
            )
            botao = discord.ui.Button(
                label=letra,
                style=estilo,
                disabled=True,
                custom_id=(
                    f"laudos:prova_done:{consulta_id}:{numero}:{letra}"
                ),
            )
            linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# Questão {numero}/{TOTAL_PERGUNTAS_PORTE}\n"
                    f"Resposta registrada: **{letra_escolhida}**"
                ),
                discord.ui.TextDisplay(
                    f"-# {barra_progresso_porte(numero, TOTAL_PERGUNTAS_PORTE)}"
                ),
                linha,
                accent_color=discord.Color.dark_grey(),
            )
        )


class BlocoConcluidoLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """Aviso de fim do bloco 1; o bloco 2 chega em mensagem nova."""

    def __init__(
        self,
        *,
        bloco: int,
        letra_escolhida: str,
        numero: int,
    ):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# ✅ Bloco {bloco} concluído\n"
                    f"Última resposta registrada: **{letra_escolhida}** "
                    f"(questão {numero}).\n"
                    "O próximo bloco foi enviado em uma mensagem nova abaixo."
                ),
                accent_color=discord.Color.green(),
            )
        )


class ProvaEnviadaLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """Card final para o paciente após enviar todas as respostas."""

    def __init__(self, *, acertos: int, parecer: str):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ✅ Avaliação enviada\n"
                    f"Você acertou **{acertos}/{TOTAL_PERGUNTAS_PORTE}**.\n"
                    "O detalhamento foi encaminhado à equipe de Psicologia.\n"
                    "O **parecer final do laudo** continua a cargo do "
                    "psicólogo responsável."
                ),
                accent_color=(
                    discord.Color.green()
                    if parecer == "APROVADO"
                    else discord.Color.orange()
                ),
            )
        )
