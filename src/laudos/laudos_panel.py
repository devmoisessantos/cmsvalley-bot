"""Painel persistente de avaliação psicológica (Components V2)."""

from __future__ import annotations

import logging

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
    responder_view,
)

registrador = logging.getLogger(__name__)


class PainelLaudosLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo no canal CANAL_PAINEL_LAUDOS."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild = guild

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []

        # Bloco 1: cabeçalho com ícone do servidor (quando existir)
        texto_cabecalho = (
            "# 🧠 **Painel de Avaliação Psicológica**\n"
            "### 🔒 Área restrita – **Equipe de Psicólogos** do CMS Valley\n"
            "-# Somente psicólogos autorizados. Uma consulta aberta por vez."
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

        # Bloco 2: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 3: sobre o sistema
        componentes.append(
            discord.ui.TextDisplay(
                "## 📋 Sobre o sistema\n"
                "Este painel é destinado exclusivamente à **equipe de Psicologia** "
                "para registro de consultas e emissão de laudos psicológicos "
                "obrigatórios para **porte de arma de fogo**.\n\n"
                "> ⚠️ **Acesso autorizado apenas a psicólogos credenciados.**\n"
                "> 🔄 **Apenas uma consulta pode estar aberta por vez.**"
            )
        )

        # Bloco 4: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bloco 5: fluxo de trabalho
        componentes.append(
            discord.ui.TextDisplay(
                "## 📌 Fluxo de trabalho\n"
                "- 1️⃣ Clique em **Iniciar Consulta** e selecione o paciente\n"
                "- 2️⃣ Realize a avaliação com base no **perfil emocional** e "
                "**estabilidade psicológica**\n"
                "- 3️⃣ Clique em **Gerar Laudo** e informe o parecer final: "
                "**Aprovado** ou **Reprovado**"
            )
        )

        # Bloco 6: separador antes dos botões
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Botões (inalterados)
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

        componentes.append(linha_botoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
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
                    "Apenas **Psicólogo** ou **Responsável Psicólogo** podem usar "
                    "este painel.",
                ],
            )
            return False
        return True

    async def _ao_iniciar_consulta(self, interacao: discord.Interaction):
        try:
            if not await self._checar_psicologo(interacao):
                return
            await responder_view(
                interacao,
                ViewSelecionarPaciente(interacao.user.id),
                ephemeral=True,
            )
        except discord.NotFound:
            return
        except discord.HTTPException as erro_http:
            registrador.warning(f"⚠️ [laudos] iniciar consulta HTTP: {erro_http}")

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
                        "Apenas **Psicólogo** ou **Responsável Psicólogo** podem "
                        "usar este painel.",
                    ],
                )
                return

            consulta = await buscar_consulta_aberta(interacao.user.id)
            if consulta is None:
                await responder_aviso(
                    interacao,
                    titulo="Consulta não iniciada",
                    linhas=[
                        "Você precisa clicar em **Iniciar Consulta** e selecionar o "
                        "paciente "
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
            registrador.warning(f"⚠️ [laudos] gerar laudo HTTP: {erro_http}")

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
            registrador.warning(f"⚠️ [laudos] cancelar consulta HTTP: {erro_http}")


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
        linha_select = discord.ui.ActionRow()
        linha_select.add_item(seletor)

        botao_por_id = discord.ui.Button(
            label="Buscar por Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔎",
            custom_id="laudos:buscar_discord_id",
        )
        botao_por_id.callback = self._ao_buscar_por_discord_id
        linha_botao = discord.ui.ActionRow()
        linha_botao.add_item(botao_por_id)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🩺 Iniciar consulta\n"
                    "Escolha o **membro** no select **ou** busque pelo Discord ID."
                ),
                linha_select,
                linha_botao,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_buscar_por_discord_id(self, interacao: discord.Interaction):
        if interacao.user.id != self.id_do_psicologo:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este botão não é seu."],
            )
            return
        try:
            await interacao.response.send_modal(
                ModalBuscarPacientePorDiscordId(id_do_psicologo=self.id_do_psicologo)
            )
        except discord.NotFound:
            return
        except discord.HTTPException as erro_http:
            registrador.warning(f"⚠️ [laudos] modal buscar ID HTTP: {erro_http}")

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

        await _finalizar_inicio_consulta_com_id(
            interacao,
            id_do_psicologo=self.id_do_psicologo,
            id_paciente=int(valores[0]),
        )


async def _finalizar_inicio_consulta_com_id(
    interacao: discord.Interaction,
    *,
    id_do_psicologo: int,
    id_paciente: int,
) -> None:
    """Resolve o membro e chama iniciar_consulta (select ou modal de Discord ID)."""
    if interacao.user.id != id_do_psicologo:
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Esta ação não é sua."],
        )
        return

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
            linhas=[
                f"Não há membro com Discord ID `{id_paciente}` neste servidor.",
            ],
        )
        return

    if not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use o painel dentro do servidor."],
        )
        return

    if not interacao.response.is_done():
        try:
            await interacao.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        except discord.HTTPException:
            return

    ok, mensagem, _consulta = await iniciar_consulta(
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
        return

    # Visitante sem passaporte no banco/apelido → botão que abre modal do ID FiveM
    precisa_passaporte = (
        ("ID FiveM" in mensagem or "passaporte" in mensagem.lower())
        and "Você já tem uma consulta" not in mensagem
        and paciente.id != interacao.user.id
    )
    if precisa_passaporte:
        await responder_view(
            interacao,
            ViewInformarPassaporte(
                id_do_psicologo=id_do_psicologo,
                id_paciente=paciente.id,
                texto_erro=mensagem,
            ),
            ephemeral=True,
        )
        return

    await responder_erro(
        interacao,
        titulo="Não foi possível iniciar",
        linhas=[mensagem],
    )


class ModalBuscarPacientePorDiscordId(
    LoggingModalMixin, discord.ui.Modal, title="🔎 Buscar por Discord ID"
):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID do paciente",
        placeholder="Ex: 859100649366356000",
        required=True,
        min_length=15,
        max_length=20,
    )

    def __init__(self, *, id_do_psicologo: int):
        super().__init__()
        self.id_do_psicologo = id_do_psicologo

    async def on_submit(self, interacao: discord.Interaction):
        """Valida o Discord ID digitado e inicia a busca do paciente escolhido."""
        texto = self.discord_id_input.value.strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números do Discord ID."],
            )
            return

        await _finalizar_inicio_consulta_com_id(
            interacao,
            id_do_psicologo=self.id_do_psicologo,
            id_paciente=int(texto),
        )


class ViewInformarPassaporte(LoggingViewMixin, discord.ui.LayoutView):
    """Quando o visitante não tem ID no banco/apelido — botão abre modal."""

    def __init__(
        self,
        *,
        id_do_psicologo: int,
        id_paciente: int,
        texto_erro: str,
    ):
        super().__init__(timeout=180)
        self.id_do_psicologo = id_do_psicologo
        self.id_paciente = id_paciente

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Informar passaporte",
            style=discord.ButtonStyle.primary,
            emoji="🪪",
            custom_id="laudos:informar_passaporte",
        )
        botao.callback = self._ao_abrir_modal
        linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🪪 Passaporte não encontrado\n"
                    f"{texto_erro}\n\n"
                    "Informe o **ID FiveM** do paciente. "
                    "O bot grava em `usuarios` e inicia a consulta."
                ),
                linha,
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_abrir_modal(self, interacao: discord.Interaction):
        if interacao.user.id != self.id_do_psicologo:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este botão não é seu."],
            )
            return
        await interacao.response.send_modal(
            ModalInformarPassaportePaciente(
                id_do_psicologo=self.id_do_psicologo,
                id_paciente=self.id_paciente,
            )
        )


class ModalInformarPassaportePaciente(
    LoggingModalMixin, discord.ui.Modal, title="🪪 Passaporte FiveM do paciente"
):
    passaporte_input = discord.ui.TextInput(
        label="ID FiveM (passaporte)",
        placeholder="Ex: 1382",
        required=True,
        min_length=1,
        max_length=7,
    )

    def __init__(self, *, id_do_psicologo: int, id_paciente: int):
        super().__init__()
        self.id_do_psicologo = id_do_psicologo
        self.id_paciente = id_paciente

    async def on_submit(self, interacao: discord.Interaction):
        """Registra um passaporte informado manualmente e inicia a consulta.

        Revalida o psicólogo, o servidor e o paciente para impedir que um modal
        antigo seja usado por outra pessoa. Ao delegar ao serviço, o passaporte
        é gravado no banco antes de a consulta ser aberta.
        """
        texto = self.passaporte_input.value.strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="Passaporte inválido",
                linhas=["Informe só números do ID FiveM (ex.: 1382)."],
            )
            return

        if interacao.user.id != self.id_do_psicologo:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Esta ação não é sua."],
            )
            return

        guilda = interacao.guild
        if guilda is None or not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use o painel dentro do servidor."],
            )
            return

        paciente = guilda.get_member(self.id_paciente)
        if paciente is None:
            try:
                paciente = await guilda.fetch_member(self.id_paciente)
            except discord.HTTPException:
                paciente = None
        if paciente is None:
            await responder_erro(
                interacao,
                titulo="Paciente não encontrado",
                linhas=[f"Discord ID `{self.id_paciente}` não está no servidor."],
            )
            return

        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)

        ok, mensagem, _consulta = await iniciar_consulta(
            psicologo=interacao.user,
            paciente=paciente,
            id_fivem_paciente_manual=texto,
        )
        if ok:
            await responder_sucesso(
                interacao,
                titulo="Consulta iniciada",
                linhas=[mensagem, f"Passaporte gravado: `{texto}`"],
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
        """Gera o laudo clínico da consulta e tenta publicá-lo nos canais.

        O serviço valida o parecer e grava o resultado no banco antes da
        publicação. Caso os canais falhem, avisa que o registro permaneceu
        salvo; no sucesso, devolve um bloco pronto para copiar ao servidor.
        """
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
                    f"O registro foi gravado, mas a publicação falhou: "
                    f"`{type(erro_pub).__name__}"
                    f"`.",
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
            delay=300,
        )
