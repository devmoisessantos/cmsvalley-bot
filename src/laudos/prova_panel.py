"""
Painel do paciente e cards empilhados da prova de porte.

Card 1 (questões 1–5): a cada resposta, a pergunta fica travada no card e
a próxima é acrescentada embaixo — nada é apagado.

Card 2 (questões 6–10): só abre quando o bloco 1 termina, em mensagem nova
logo abaixo (empilhada).
"""

from __future__ import annotations

import logging

import discord

from src.database.conexao import async_session
from src.database.models import ConsultaLaudo
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
                "- As perguntas ficam **empilhadas** no mesmo card\n"
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
        """Valida liberação e abre o card 1 (questão 1)."""
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

            view_bloco = BlocoProvaLayoutView(
                consulta_id=consulta.id,
                numero_atual=1,
                respostas={},
                guild=interacao.guild,
            )
            await responder_view(
                interacao,
                view_bloco,
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


def _intervalo_do_bloco(numero: int) -> tuple[int, int]:
    """Retorna (primeira, última) pergunta do bloco que contém `numero`."""
    if numero <= TAMANHO_BLOCO_PORTE:
        return 1, TAMANHO_BLOCO_PORTE
    return TAMANHO_BLOCO_PORTE + 1, TOTAL_PERGUNTAS_PORTE


def _numero_do_bloco(numero: int) -> int:
    return 1 if numero <= TAMANHO_BLOCO_PORTE else 2


class BlocoProvaLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Um card de bloco: perguntas já respondidas empilhadas + a atual ativa.

    respostas: mapa numero → letra escolhida das perguntas já gravadas
    neste bloco (e só neste bloco).
    """

    def __init__(
        self,
        *,
        consulta_id: int,
        numero_atual: int,
        respostas: dict[int, str],
        guild: discord.Guild | None,
        bloco_concluido: bool = False,
    ):
        super().__init__(timeout=None)
        self.consulta_id = consulta_id
        self.numero_atual = numero_atual
        self.respostas = dict(respostas)
        self.guild = guild
        self.bloco_concluido = bloco_concluido

        primeira, ultima = _intervalo_do_bloco(numero_atual)
        numero_bloco = _numero_do_bloco(numero_atual)

        componentes: list = [
            discord.ui.TextDisplay(
                f"# 📝 Prova de Porte — Bloco {numero_bloco}\n"
                f"-# {barra_progresso_porte(numero_atual if not bloco_concluido else ultima, TOTAL_PERGUNTAS_PORTE)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        # Perguntas já respondidas neste bloco (travadas)
        for numero in range(primeira, numero_atual):
            letra = self.respostas.get(numero)
            if letra is None:
                continue
            componentes.extend(
                self._componentes_pergunta_travada(numero, letra)
            )

        if bloco_concluido:
            letra_ultima = self.respostas.get(ultima)
            if letra_ultima is not None:
                componentes.extend(
                    self._componentes_pergunta_travada(ultima, letra_ultima)
                )
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            componentes.append(
                discord.ui.TextDisplay(
                    f"# ✅ Bloco {numero_bloco} concluído\n"
                    + (
                        "O próximo bloco foi enviado em uma mensagem nova "
                        "abaixo."
                        if numero_bloco == 1
                        else "Todas as perguntas foram respondidas."
                    )
                )
            )
        else:
            # Pergunta atual com botões ativos
            componentes.extend(
                self._componentes_pergunta_ativa(numero_atual)
            )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    def _componentes_pergunta_travada(
        self, numero: int, letra_escolhida: str
    ) -> list:
        """Texto da pergunta + A/B/C desabilitados (escolhida em destaque)."""
        pergunta = obter_pergunta(numero)
        if pergunta is None:
            return []

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
                    f"laudos:prova_lock:{self.consulta_id}:"
                    f"{numero}:{letra}"
                ),
            )
            linha.add_item(botao)

        enunciado = (
            f"### Questão {numero}/{TOTAL_PERGUNTAS_PORTE}\n"
            f"{pergunta.enunciado}\n"
            f"**A)** {pergunta.opcao_a}\n"
            f"**B)** {pergunta.opcao_b}\n"
            f"**C)** {pergunta.opcao_c}\n"
            f"-# Resposta: **{letra_escolhida}**"
        )
        return [
            discord.ui.TextDisplay(enunciado),
            linha,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

    def _componentes_pergunta_ativa(self, numero: int) -> list:
        """Pergunta corrente com botões A/B/C clicáveis."""
        pergunta = obter_pergunta(numero)
        if pergunta is None:
            return [discord.ui.TextDisplay("Pergunta não encontrada.")]

        linha = discord.ui.ActionRow()
        for letra in ("A", "B", "C"):
            botao = discord.ui.Button(
                label=letra,
                style=discord.ButtonStyle.primary,
                custom_id=(
                    f"laudos:prova:{self.consulta_id}:{numero}:{letra}"
                ),
            )
            botao.callback = self._fazer_callback(letra)
            linha.add_item(botao)

        enunciado = (
            f"### Questão {numero}/{TOTAL_PERGUNTAS_PORTE}\n"
            f"{pergunta.enunciado}\n"
            f"**A)** {pergunta.opcao_a}\n"
            f"**B)** {pergunta.opcao_b}\n"
            f"**C)** {pergunta.opcao_c}"
        )
        return [
            discord.ui.TextDisplay(enunciado),
            linha,
        ]

    def _fazer_callback(self, letra: str):
        async def _ao_escolher(interacao: discord.Interaction):
            await self._processar_resposta(interacao, letra)

        return _ao_escolher

    async def _processar_resposta(
        self,
        interacao: discord.Interaction,
        letra: str,
    ):
        """Grava a escolha e reempilha o card com a próxima pergunta."""
        ok, mensagem, _correta = await gravar_resposta_prova(
            consulta_id=self.consulta_id,
            discord_id_paciente=interacao.user.id,
            numero_pergunta=self.numero_atual,
            letra_escolhida=letra,
        )
        if not ok:
            await responder_erro(
                interacao,
                titulo="Não registrei a resposta",
                linhas=[mensagem],
            )
            return

        novas_respostas = dict(self.respostas)
        novas_respostas[self.numero_atual] = letra.upper()
        primeira, ultima = _intervalo_do_bloco(self.numero_atual)

        # Fim do bloco 1 → trava card 1 e abre card 2 embaixo
        if self.numero_atual == TAMANHO_BLOCO_PORTE:
            view_bloco1 = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual,
                respostas=novas_respostas,
                guild=interacao.guild,
                bloco_concluido=True,
            )
            await editar_mensagem_original(interacao, view=view_bloco1)

            view_bloco2 = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual + 1,
                respostas={},
                guild=interacao.guild,
            )
            await interacao.followup.send(
                view=view_bloco2,
                ephemeral=True,
            )
            return

        # Fim da prova (questão 10)
        if self.numero_atual >= TOTAL_PERGUNTAS_PORTE:
            view_final = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual,
                respostas=novas_respostas,
                guild=interacao.guild,
                bloco_concluido=True,
            )
            await editar_mensagem_original(interacao, view=view_final)
            await self._concluir_prova(interacao)
            return

        # Continua no mesmo bloco: empilha a próxima
        view_proxima = BlocoProvaLayoutView(
            consulta_id=self.consulta_id,
            numero_atual=self.numero_atual + 1,
            respostas=novas_respostas,
            guild=interacao.guild,
        )
        await editar_mensagem_original(interacao, view=view_proxima)

    async def _concluir_prova(self, interacao: discord.Interaction):
        """Fecha a prova no banco e publica o resultado para a equipe."""
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

        # Aviso curto ao paciente (followup, o card 2 já está travado)
        try:
            await interacao.followup.send(
                content=(
                    f"Avaliação enviada. Você acertou "
                    f"**{acertos}/{TOTAL_PERGUNTAS_PORTE}**. "
                    "O detalhamento foi para a equipe de Psicologia."
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            pass
