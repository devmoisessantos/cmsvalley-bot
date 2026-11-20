"""
Painel do paciente e cards empilhados da prova de laudo.

Card 1 (questões 1–5): a cada resposta, a pergunta fica travada no card e
a próxima é acrescentada embaixo — nada é apagado.

Card 2 (questões 6–10): só abre quando o bloco 1 termina, em mensagem nova
logo abaixo (empilhada).

O banco de perguntas depende do tipo_laudo da consulta
(PORTE ou LAUDO_ADOCAO).
"""

from __future__ import annotations

import logging

import discord

from src.database.conexao import async_session
from src.database.models import ConsultaLaudo
from src.laudos.perguntas_laudo import (
    obter_pergunta,
    tamanho_bloco,
    total_perguntas,
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
            "# 🧠 Avaliação Psicológica — Prova Teórica\n"
            "### Prova obrigatória para laudo (porte ou adoção)\n"
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
                "- **10 questões** de múltipla escolha (2 blocos de 5)\n"
                "- O conteúdo depende da modalidade da consulta "
                "(porte ou adoção)\n"
                "- **Uma única tentativa** por consulta\n"
                "- As perguntas ficam **empilhadas** no mesmo card\n"
                "- Feedback e gabarito **somente para a equipe** no final\n"
                "- Critério: **7/10** acertos na prova "
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

            tipo = getattr(consulta, "tipo_laudo", None) or "PORTE"
            view_bloco = BlocoProvaLayoutView(
                consulta_id=consulta.id,
                numero_atual=1,
                respostas={},
                guild=interacao.guild,
                tipo_laudo=tipo,
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


def _intervalo_do_bloco(numero: int, tipo: str | None = "PORTE") -> tuple[int, int]:
    """Retorna (primeira, última) pergunta do bloco que contém `numero`."""
    bloco = tamanho_bloco(tipo)
    total = total_perguntas(tipo)
    if numero <= bloco:
        return 1, bloco
    return bloco + 1, total


def _numero_do_bloco(numero: int, tipo: str | None = "PORTE") -> int:
    bloco = tamanho_bloco(tipo)
    return 1 if numero <= bloco else 2


class BlocoProvaLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Um card de bloco: perguntas já respondidas empilhadas + a atual ativa.

    respostas: mapa numero → letra escolhida das perguntas já gravadas
    neste bloco (e só neste bloco).

    aguardando_envio: bloco 2 completo; mostra o botão Enviar avaliação.
    """

    def __init__(
        self,
        *,
        consulta_id: int,
        numero_atual: int,
        respostas: dict[int, str],
        guild: discord.Guild | None,
        bloco_concluido: bool = False,
        aguardando_envio: bool = False,
        tipo_laudo: str = "PORTE",
    ):
        super().__init__(timeout=None)
        self.consulta_id = consulta_id
        self.numero_atual = numero_atual
        self.respostas = dict(respostas)
        self.guild = guild
        self.bloco_concluido = bloco_concluido
        self.aguardando_envio = aguardando_envio
        self.tipo_laudo = tipo_laudo or "PORTE"

        primeira, ultima = _intervalo_do_bloco(numero_atual, self.tipo_laudo)
        numero_bloco = _numero_do_bloco(numero_atual, self.tipo_laudo)
        progresso = ultima if (bloco_concluido or aguardando_envio) else numero_atual
        total = total_perguntas(self.tipo_laudo)

        componentes: list = [
            discord.ui.TextDisplay(
                f"# 📝 Prova teórica — Bloco {numero_bloco}\n"
                f"-# {barra_progresso_porte(progresso, total)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        # Perguntas já respondidas neste bloco (travadas)
        if bloco_concluido or aguardando_envio:
            numeros_travados = range(primeira, ultima + 1)
        else:
            numeros_travados = range(primeira, numero_atual)

        for numero in numeros_travados:
            letra = self.respostas.get(numero)
            if letra is None:
                continue
            componentes.extend(
                self._componentes_pergunta_travada(numero, letra)
            )

        if aguardando_envio:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            componentes.append(
                discord.ui.TextDisplay(
                    "# ✅ Bloco 2 concluído\n"
                    "Revise se quiser. Clique em **Enviar avaliação** "
                    "para finalizar."
                )
            )
            linha_enviar = discord.ui.ActionRow()
            botao_enviar = discord.ui.Button(
                label="Enviar avaliação",
                style=discord.ButtonStyle.success,
                emoji="📨",
                custom_id=f"laudos:enviar_prova:{consulta_id}",
            )
            botao_enviar.callback = self._ao_enviar_avaliacao
            linha_enviar.add_item(botao_enviar)
            componentes.append(linha_enviar)
        elif bloco_concluido:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            componentes.append(
                discord.ui.TextDisplay(
                    f"# ✅ Bloco {numero_bloco} concluído\n"
                    "O próximo bloco foi enviado em uma mensagem nova abaixo."
                )
            )
        else:
            componentes.extend(
                self._componentes_pergunta_ativa(numero_atual)
            )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_enviar_avaliacao(self, interacao: discord.Interaction):
        """Finaliza a prova quando o paciente confirma o envio."""
        await editar_mensagem_original(
            interacao,
            view=ViewProvaEnviadaLayout(
                respostas=self.respostas,
                tipo_laudo=self.tipo_laudo,
            ),
        )
        await self._concluir_prova(interacao)

    def _componentes_pergunta_travada(
        self, numero: int, letra_escolhida: str
    ) -> list:
        """
        Texto da pergunta + A/B/C desabilitados com cor do resultado.

        - Acertou: so a escolhida em verde (success).
        - Errou: escolhida em vermelho (danger) e a correta em verde.
        - Demais: cinza (secondary).
        """
        pergunta = obter_pergunta(self.tipo_laudo, numero)
        if pergunta is None:
            return []

        total = total_perguntas(self.tipo_laudo)
        letra_correta = (pergunta.resposta_correta or "").upper()
        letra_escolhida = (letra_escolhida or "").upper()
        acertou = letra_escolhida == letra_correta

        linha = discord.ui.ActionRow()
        for letra in ("A", "B", "C"):
            if acertou:
                if letra == letra_escolhida:
                    estilo = discord.ButtonStyle.success
                else:
                    estilo = discord.ButtonStyle.secondary
            else:
                if letra == letra_escolhida:
                    estilo = discord.ButtonStyle.danger
                elif letra == letra_correta:
                    estilo = discord.ButtonStyle.success
                else:
                    estilo = discord.ButtonStyle.secondary

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
            f"### Questão {numero}/{total}\n"
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
        pergunta = obter_pergunta(self.tipo_laudo, numero)
        if pergunta is None:
            return [discord.ui.TextDisplay("Pergunta não encontrada.")]

        total = total_perguntas(self.tipo_laudo)
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
            f"### Questão {numero}/{total}\n"
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
        primeira, ultima = _intervalo_do_bloco(
            self.numero_atual, self.tipo_laudo
        )
        bloco = tamanho_bloco(self.tipo_laudo)
        total = total_perguntas(self.tipo_laudo)

        # Fim do bloco 1 → trava card 1 e abre card 2 embaixo
        if self.numero_atual == bloco:
            view_bloco1 = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual,
                respostas=novas_respostas,
                guild=interacao.guild,
                bloco_concluido=True,
                tipo_laudo=self.tipo_laudo,
            )
            await editar_mensagem_original(interacao, view=view_bloco1)

            view_bloco2 = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual + 1,
                respostas={},
                guild=interacao.guild,
                tipo_laudo=self.tipo_laudo,
            )
            await interacao.followup.send(
                view=view_bloco2,
                ephemeral=True,
            )
            return

        # Fim da prova → card com botão Enviar avaliação
        if self.numero_atual >= total:
            view_final = BlocoProvaLayoutView(
                consulta_id=self.consulta_id,
                numero_atual=self.numero_atual,
                respostas=novas_respostas,
                guild=interacao.guild,
                aguardando_envio=True,
                tipo_laudo=self.tipo_laudo,
            )
            await editar_mensagem_original(interacao, view=view_final)
            return

        # Continua no mesmo bloco: empilha a próxima
        view_proxima = BlocoProvaLayoutView(
            consulta_id=self.consulta_id,
            numero_atual=self.numero_atual + 1,
            respostas=novas_respostas,
            guild=interacao.guild,
            tipo_laudo=self.tipo_laudo,
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

        total = total_perguntas(self.tipo_laudo)
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

        try:
            await interacao.followup.send(
                content=(
                    f"Avaliação enviada. Você acertou "
                    f"**{acertos}/{total}**. "
                    "O detalhamento foi para a equipe de Psicologia."
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            return


class ViewProvaEnviadaLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Card final do bloco 2 após o paciente enviar a avaliação."""

    def __init__(self, *, respostas: dict[int, str], tipo_laudo: str = "PORTE"):
        super().__init__(timeout=None)
        total = total_perguntas(tipo_laudo)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ✅ Avaliação enviada\n"
                    f"-# {barra_progresso_porte(total, total)}\n"
                    "Suas respostas foram registradas. "
                    "A equipe de Psicologia recebeu o resultado."
                ),
                accent_color=discord.Color.green(),
            )
        )
