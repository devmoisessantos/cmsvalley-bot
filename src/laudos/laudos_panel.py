"""Painel persistente de avaliação psicológica (Components V2)."""

from __future__ import annotations

import discord

from src.laudos.laudos_logger import publicar_laudo_nos_canais
from src.laudos.laudos_service import (
    buscar_consulta_aberta,
    cancelar_consulta_aberta,
    gerar_laudo,
    iniciar_consulta,
    membro_e_psicologo,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

TEXTO_PAINEL = (
    "# 🧠 Painel de Avaliação Psicológica\n\n"
    "Área exclusiva da **equipe de Psicologia** do CMS Valley.\n\n"
    "Aqui você registra a **consulta** e emite o **laudo psicológico** "
    "exigido para análise de **porte de arma de fogo**.\n\n"
    "**Fluxo obrigatório**\n"
    "1. Clique em **Iniciar Consulta** e selecione o paciente\n"
    "2. Realize a avaliação com base no perfil e estabilidade emocional\n"
    "3. Clique em **Gerar Laudo** e informe o parecer (aprovado ou reprovado)\n\n"
    "-# Somente psicólogos autorizados. Uma consulta aberta por vez."
)


class PainelLaudosLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo no canal CANAL_PAINEL_LAUDOS."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild = guild
        icon_url = guild.icon.url if guild and guild.icon else None

        if icon_url:
            cabecalho = discord.ui.Section(
                TEXTO_PAINEL,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        else:
            cabecalho = discord.ui.TextDisplay(TEXTO_PAINEL)

        linha_botoes = discord.ui.ActionRow()
        botao_iniciar = discord.ui.Button(
            label="Iniciar Consulta",
            style=discord.ButtonStyle.primary,
            emoji="🩺",
            custom_id="laudos:iniciar_consulta",
        )
        botao_iniciar.callback = self._ao_iniciar_consulta
        linha_botoes.add_item(botao_iniciar)

        botao_laudo = discord.ui.Button(
            label="Gerar Laudo",
            style=discord.ButtonStyle.success,
            emoji="📋",
            custom_id="laudos:gerar_laudo",
        )
        botao_laudo.callback = self._ao_gerar_laudo
        linha_botoes.add_item(botao_laudo)

        botao_cancelar = discord.ui.Button(
            label="Cancelar Consulta",
            style=discord.ButtonStyle.secondary,
            emoji="🗑️",
            custom_id="laudos:cancelar_consulta",
        )
        botao_cancelar.callback = self._ao_cancelar_consulta
        linha_botoes.add_item(botao_cancelar)

        self.add_item(
            discord.ui.Container(
                cabecalho,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_botoes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _checar_psicologo(self, interacao: discord.Interaction) -> bool:
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este painel dentro do servidor."],
            )
            return False
        if not membro_e_psicologo(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Apenas **Psicólogo** ou **Responsável Psicólogo** podem usar este painel.",
                ],
            )
            return False
        return True

    async def _ao_iniciar_consulta(self, interacao: discord.Interaction):
        try:
            if not await self._checar_psicologo(interacao):
                return
            await interacao.response.send_message(
                view=ViewSelecionarPaciente(interacao.user.id),
                ephemeral=True,
            )
        except discord.NotFound:
            return
        except discord.HTTPException as erro_http:
            print(f"⚠️ [laudos] iniciar consulta HTTP: {erro_http}")

    async def _ao_gerar_laudo(self, interacao: discord.Interaction):
        """Abre o modal do laudo se houver consulta aberta."""
        try:
            if not isinstance(interacao.user, discord.Member):
                await responder_erro(
                    interacao,
                    titulo="Contexto inválido",
                    linhas=["Use este painel dentro do servidor."],
                )
                return
            if not membro_e_psicologo(interacao.user):
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=[
                        "Apenas **Psicólogo** ou **Responsável Psicólogo** podem usar este painel.",
                    ],
                )
                return

            consulta = await buscar_consulta_aberta(interacao.user.id)
            if consulta is None:
                await responder_aviso(
                    interacao,
                    titulo="Consulta não iniciada",
                    linhas=[
                        "Você precisa clicar em **Iniciar Consulta** e selecionar o paciente "
                        "antes de gerar o laudo.",
                    ],
                )
                return

            if interacao.response.is_done():
                return
            await interacao.response.send_modal(
                ModalGerarLaudo(
                    consulta_id=consulta.id,
                    paciente_id=consulta.discord_id_paciente,
                )
            )
        except discord.NotFound:
            return
        except discord.HTTPException as erro_http:
            print(f"⚠️ [laudos] gerar laudo HTTP: {erro_http}")

    async def _ao_cancelar_consulta(self, interacao: discord.Interaction):
        try:
            if not await self._checar_psicologo(interacao):
                return
            ok, mensagem = await cancelar_consulta_aberta(interacao.user.id)
            if ok:
                await responder_sucesso(
                    interacao,
                    titulo="Consulta cancelada",
                    linhas=[mensagem],
                )
            else:
                await responder_aviso(
                    interacao,
                    titulo="Nada a cancelar",
                    linhas=[mensagem],
                )
        except discord.NotFound:
            return
        except discord.HTTPException as erro_http:
            print(f"⚠️ [laudos] cancelar consulta HTTP: {erro_http}")


class ViewSelecionarPaciente(LoggingViewMixin, discord.ui.LayoutView):
    """Select efêmero para escolher o paciente da consulta."""

    def __init__(self, id_do_psicologo: int):
        super().__init__(timeout=180)
        self.id_do_psicologo = id_do_psicologo

        seletor = discord.ui.UserSelect(
            placeholder="Selecione o paciente avaliado…",
            min_values=1,
            max_values=1,
            custom_id="laudos:select_paciente",
        )
        seletor.callback = self._ao_escolher_paciente
        linha = discord.ui.ActionRow()
        linha.add_item(seletor)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🩺 Iniciar consulta\n"
                    "Escolha o **membro** que será avaliado nesta consulta."
                ),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_escolher_paciente(self, interacao: discord.Interaction):
        if interacao.user.id != self.id_do_psicologo:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este seletor não é seu."],
            )
            return

        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção vazia",
                linhas=["Nenhum paciente foi selecionado."],
            )
            return

        id_paciente = int(valores[0])
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Servidor ausente",
                linhas=["Não foi possível resolver o servidor."],
            )
            return

        paciente = guilda.get_member(id_paciente)
        if paciente is None:
            try:
                paciente = await guilda.fetch_member(id_paciente)
            except discord.HTTPException:
                paciente = None
        if paciente is None:
            await responder_erro(
                interacao,
                titulo="Paciente não encontrado",
                linhas=["O membro selecionado não está no servidor."],
            )
            return

        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use o painel dentro do servidor."],
            )
            return

        # Defer antes do banco — evita 10062 se a gravação demorar
        if not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.NotFound:
                return
            except discord.HTTPException:
                return

        ok, mensagem, consulta = await iniciar_consulta(
            psicologo=interacao.user,
            paciente=paciente,
        )
        if ok:
            await responder_sucesso(
                interacao,
                titulo="Consulta iniciada",
                linhas=[mensagem],
                delay=20,
            )
        else:
            await responder_erro(
                interacao,
                titulo="Não foi possível iniciar",
                linhas=[mensagem],
            )


class ModalGerarLaudo(LoggingModalMixin, discord.ui.Modal, title="📋 Gerar Laudo"):
    parecer_input = discord.ui.TextInput(
        label="Parecer (APROVADO ou REPROVADO)",
        placeholder="APROVADO",
        required=True,
        max_length=12,
    )
    motivo_input = discord.ui.TextInput(
        label="Motivo / fundamentação",
        style=discord.TextStyle.paragraph,
        placeholder="Descreva o parecer clínico de forma objetiva…",
        required=True,
        max_length=1500,
    )

    def __init__(self, *, consulta_id: int, paciente_id: int):
        super().__init__()
        self.consulta_id = consulta_id
        self.paciente_id = paciente_id

    async def on_submit(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use o painel dentro do servidor."],
            )
            return

        await interacao.response.defer(ephemeral=True)

        ok, mensagem, laudo, texto_laudo, texto_yaml = await gerar_laudo(
            psicologo=interacao.user,
            parecer=self.parecer_input.value,
            motivo=self.motivo_input.value,
        )
        if not ok or laudo is None or texto_laudo is None:
            await responder_erro(
                interacao,
                titulo="Laudo não gerado",
                linhas=[mensagem],
            )
            return

        guilda = interacao.guild
        paciente = guilda.get_member(self.paciente_id) if guilda else None
        try:
            if guilda is not None:
                await publicar_laudo_nos_canais(
                    guild=guilda,
                    texto_laudo=texto_laudo,
                    laudo=laudo,
                    psicologo=interacao.user,
                    paciente=paciente,
                )
        except Exception as erro_pub:
            await responder_aviso(
                interacao,
                titulo="Laudo salvo com aviso",
                linhas=[
                    mensagem,
                    f"O registro foi gravado, mas a publicação falhou: `{type(erro_pub).__name__}`.",
                ],
            )
            return

        # Ephemeral: resumo + bloco para copiar no Valley Roleplay
        bloco_copiar = (
            f"```yaml\n{texto_yaml}\n```" if texto_yaml else "_Sem texto para copiar._"
        )
        await responder_sucesso(
            interacao,
            titulo="Laudo publicado",
            linhas=[
                mensagem,
                "O documento foi enviado ao canal de laudos e ao log interno.",
                "—",
                "**Copie o bloco abaixo** para colar no servidor Valley Roleplay:",
                bloco_copiar,
            ],
            delay=120,
        )
