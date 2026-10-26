"""
Avaliação de atendimento: painel fixo + fluxo interativo.

O painel persistente mora em CANAL_AVALIAR_ATENDIMENTO. As avaliações
confirmadas são publicadas em MURAL_AVALIACOES. Só dá para avaliar membros
que tenham cargo da hierarquia hospitalar.
"""

from __future__ import annotations

import discord

from src.config import CANAIS, CARGOS_HIERARQUIA
from src.membros.membros_service import resolver_discord_id_por_fivem
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.formatacao import agora_brasilia
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_view,
)
from src.utils.notificacao import COR_INFO, enviar_dm_card

CUSTOM_ID_INICIAR_AVALIACAO = "avaliacao_atendimento:iniciar"

# Notas 1 e 2 exigem comentário com pelo menos este tamanho.
TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA = 10


def membro_esta_na_hierarquia(membro: discord.Member) -> bool:
    """True se o membro tem algum cargo da hierarquia hospitalar."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_HIERARQUIA)))


def montar_estrelas(nota: int) -> str:
    """Ex.: 3 → ⭐⭐⭐☆☆"""
    nota_limitada = max(1, min(5, int(nota)))
    return "⭐" * nota_limitada + "☆" * (5 - nota_limitada)


def formatar_data_avaliacao() -> str:
    """Ex.: 13/08/2026 às 18:40"""
    local = agora_brasilia()
    return (
        f"{local.day:02d}/{local.month:02d}/{local.year} às "
        f"{local.strftime('%H:%M')}"
    )


def montar_corpo_avaliacao(
    avaliador: discord.abc.User,
    membro_avaliado: discord.abc.User,
    nota: int,
    comentario: str,
) -> str:
    """Texto central do card público e do rascunho."""
    texto_comentario = (comentario or "").strip()
    if texto_comentario:
        bloco_comentario = f"> ```{texto_comentario}```"
    else:
        bloco_comentario = "> _Sem comentário._"

    estrelas = montar_estrelas(nota)
    return (
        f"`👤` * **Avaliador:** {avaliador.mention}\n"
        f"`⚕️` * **Para:** {membro_avaliado.mention}\n"
        f"`⭐` * **Nota:** {estrelas} · **{nota}/5**\n"
        f"{bloco_comentario}"
    )


def montar_view_publica(
    avaliador: discord.abc.User,
    membro_avaliado: discord.Member,
    nota: int,
    comentario: str,
) -> discord.ui.LayoutView:
    """Card que vai para o mural de avaliações."""
    data_txt = formatar_data_avaliacao()
    corpo = montar_corpo_avaliacao(avaliador, membro_avaliado, nota, comentario)
    canal_painel_id = CANAIS.get("CANAL_AVALIAR_ATENDIMENTO") or 0
    if canal_painel_id:
        dica = (
            f"> Avalie no painel de <#{canal_painel_id}> "
            "ou use **/avaliar-atendimento**."
        )
    else:
        dica = (
            "> Use **/avaliar-atendimento** para fazer uma avaliação."
        )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# ⭐ Avaliação de Atendimento\n{dica}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.Section(
                corpo,
                accessory=discord.ui.Thumbnail(membro_avaliado.display_avatar.url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"-# ✅ *Avaliação registrada com sucesso!* · {data_txt}"
            ),
            accent_color=discord.Color.gold(),
        )
    )
    return view


async def validar_membro_avaliavel(
    interacao: discord.Interaction,
    membro: discord.Member | None,
) -> discord.Member | None:
    """Confere se o alvo pode receber avaliação e responde aviso se não puder."""
    if membro is None:
        await responder_erro(
            interacao,
            titulo="Não encontrado",
            linhas=["Esse membro não está no servidor."],
        )
        return None

    if membro.bot:
        await responder_aviso(
            interacao,
            titulo="Membro inválido",
            linhas=["Não é possível avaliar um bot."],
        )
        return None

    if membro.id == interacao.user.id:
        await responder_aviso(
            interacao,
            titulo="Autoavaliação",
            linhas=["Você não pode avaliar a si mesmo."],
        )
        return None

    if not membro_esta_na_hierarquia(membro):
        await responder_aviso(
            interacao,
            titulo="Fora da hierarquia",
            linhas=[
                "Só é possível avaliar membros da **hierarquia** hospitalar "
                "(enfermeiro, paramédico, doutor, etc.).",
            ],
        )
        return None

    return membro


# ---------------------------------------------------------------------------
# Painel fixo persistente
# ---------------------------------------------------------------------------


class PainelAvaliarAtendimentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente no canal CANAL_AVALIAR_ATENDIMENTO."""

    def __init__(self, guilda: discord.Guild | None = None):
        super().__init__(timeout=None)

        url_icone = None
        if guilda is not None and guilda.icon is not None:
            url_icone = guilda.icon.url

        texto_cabecalho = (
            "# ⭐ Avaliação de Atendimento\n"
            "> CMS Valley — feedback do atendimento recebido\n"
            "Use este painel para registrar como foi o atendimento de um "
            "membro da **hierarquia** hospitalar."
        )

        componentes: list = []
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_cabecalho,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_cabecalho))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay(
                "## Como funciona\n\n"
                "1. Clique em **Avaliar atendimento**\n"
                "2. Escolha quem atendeu (menção, Discord ID ou ID FiveM)\n"
                "3. Dê a nota de **1 a 5 estrelas**\n"
                "4. Escreva um comentário (obrigatório em notas 1 e 2)\n"
                "5. Confira o rascunho e confirme\n\n"
                "- Só membros da **hierarquia** podem ser avaliados\n"
                "- A avaliação publicada aparece no mural de avaliações\n"
                "- O profissional avaliado recebe um aviso na DM"
            )
        )
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Avaliar atendimento",
            emoji="⭐",
            style=discord.ButtonStyle.primary,
            custom_id=CUSTOM_ID_INICIAR_AVALIACAO,
        )
        botao.callback = self._ao_iniciar
        linha.add_item(botao)
        componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.gold(),
            )
        )

    async def _ao_iniciar(self, interacao: discord.Interaction):
        """Abre o formulário efêmero de avaliação."""
        await iniciar_fluxo_avaliacao(interacao)


# ---------------------------------------------------------------------------
# Formulário principal (membro + estrelas + prosseguir)
# ---------------------------------------------------------------------------


class ViewFormularioAvaliacao(LoggingViewMixin, discord.ui.LayoutView):
    """Tela em que o membro escolhe quem avaliar e a nota."""

    def __init__(
        self,
        *,
        membro_id: int | None = None,
        nota: int | None = None,
        nome_membro: str | None = None,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro_id
        self.nota = nota
        self.nome_membro = nome_membro

        if membro_id is not None and nome_membro:
            texto_membro = f"**Membro:** {nome_membro} (`{membro_id}`)"
        elif membro_id is not None:
            texto_membro = f"**Membro:** <@{membro_id}>"
        else:
            texto_membro = "**Membro:** _ainda não escolhido_"

        if nota is not None:
            texto_nota = f"**Nota:** {montar_estrelas(nota)} · **{nota}/5**"
        else:
            texto_nota = "**Nota:** _ainda não escolhida_"

        linha_select = discord.ui.ActionRow()
        seletor = discord.ui.UserSelect(
            placeholder="Selecione quem atendeu…",
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_selecionar_membro
        linha_select.add_item(seletor)

        linha_busca = discord.ui.ActionRow()
        botao_discord = discord.ui.Button(
            label="Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        botao_discord.callback = self._ao_pedir_discord_id
        botao_fivem = discord.ui.Button(
            label="ID FiveM",
            style=discord.ButtonStyle.secondary,
            emoji="🪪",
        )
        botao_fivem.callback = self._ao_pedir_fivem_id
        linha_busca.add_item(botao_discord)
        linha_busca.add_item(botao_fivem)

        linha_estrelas = discord.ui.ActionRow()
        for valor in range(1, 6):
            estilo = (
                discord.ButtonStyle.primary
                if self.nota == valor
                else discord.ButtonStyle.secondary
            )
            botao_estrela = discord.ui.Button(
                label=str(valor),
                style=estilo,
                emoji="⭐",
            )
            botao_estrela.callback = self._fazer_callback_nota(valor)
            linha_estrelas.add_item(botao_estrela)

        linha_acao = discord.ui.ActionRow()
        botao_prosseguir = discord.ui.Button(
            label="Prosseguir",
            style=discord.ButtonStyle.success,
            emoji="➡️",
            disabled=(self.membro_id is None or self.nota is None),
        )
        botao_prosseguir.callback = self._ao_prosseguir
        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
        )
        botao_cancelar.callback = self._ao_cancelar
        linha_acao.add_item(botao_prosseguir)
        linha_acao.add_item(botao_cancelar)

        aviso_nota_baixa = ""
        if self.nota is not None and self.nota <= 2:
            aviso_nota_baixa = (
                "\n\n-# Notas **1** e **2** exigem um comentário "
                f"com pelo menos **{TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA}** "
                "caracteres."
            )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# ⭐ Avaliar atendimento"),
                discord.ui.TextDisplay(
                    "Escolha quem atendeu (só hierarquia), a nota e "
                    "depois clique em **Prosseguir**."
                    f"{aviso_nota_baixa}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(f"{texto_membro}\n{texto_nota}"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_select,
                linha_busca,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay("**Nota (1 a 5 estrelas)**"),
                linha_estrelas,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_acao,
                accent_color=discord.Color.gold(),
            )
        )

    def _fazer_callback_nota(self, valor: int):
        async def _callback(interacao: discord.Interaction):
            await self._ao_escolher_nota(interacao, valor)

        return _callback

    async def _ao_selecionar_membro(self, interacao: discord.Interaction):
        valores = interacao.data.get("values") or []
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção vazia",
                linhas=["Nenhum membro foi selecionado."],
            )
            return

        membro = None
        if interacao.guild is not None:
            membro = interacao.guild.get_member(int(valores[0]))

        membro = await validar_membro_avaliavel(interacao, membro)
        if membro is None:
            return

        view = ViewFormularioAvaliacao(
            membro_id=membro.id,
            nota=self.nota,
            nome_membro=membro.display_name,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)

    async def _ao_pedir_discord_id(self, interacao: discord.Interaction):
        await interacao.response.send_modal(
            ModalBuscarDiscordIdAvaliacao(
                nota=self.nota,
                membro_id_atual=self.membro_id,
                nome_membro_atual=self.nome_membro,
            )
        )

    async def _ao_pedir_fivem_id(self, interacao: discord.Interaction):
        await interacao.response.send_modal(
            ModalBuscarFivemIdAvaliacao(
                nota=self.nota,
                membro_id_atual=self.membro_id,
                nome_membro_atual=self.nome_membro,
            )
        )

    async def _ao_escolher_nota(self, interacao: discord.Interaction, valor: int):
        view = ViewFormularioAvaliacao(
            membro_id=self.membro_id,
            nota=valor,
            nome_membro=self.nome_membro,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)

    async def _ao_prosseguir(self, interacao: discord.Interaction):
        if self.membro_id is None or self.nota is None:
            await responder_aviso(
                interacao,
                titulo="Falta informação",
                linhas=["Escolha o membro e a nota antes de continuar."],
            )
            return

        await interacao.response.send_modal(
            ModalComentarioAvaliacao(
                membro_id=self.membro_id,
                nota=self.nota,
                nome_membro=self.nome_membro or "",
            )
        )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        await editar_mensagem_original(
            interacao,
            titulo="Avaliação cancelada",
            linhas=["Nada foi enviado."],
            texto=None,
        )


# ---------------------------------------------------------------------------
# Busca por Discord ID / FiveM ID
# ---------------------------------------------------------------------------


class ModalBuscarDiscordIdAvaliacao(
    LoggingModalMixin, discord.ui.Modal, title="Discord ID do atendente"
):
    campo_id = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 123456789012345678",
        required=True,
        max_length=20,
    )

    def __init__(
        self,
        *,
        nota: int | None,
        membro_id_atual: int | None,
        nome_membro_atual: str | None,
    ):
        super().__init__()
        self.nota = nota
        self.membro_id_atual = membro_id_atual
        self.nome_membro_atual = nome_membro_atual

    async def on_submit(self, interacao: discord.Interaction):
        texto = (self.campo_id.value or "").strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números."],
            )
            return

        membro = None
        if interacao.guild is not None:
            membro = interacao.guild.get_member(int(texto))

        membro = await validar_membro_avaliavel(interacao, membro)
        if membro is None:
            return

        view = ViewFormularioAvaliacao(
            membro_id=membro.id,
            nota=self.nota,
            nome_membro=membro.display_name,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)


class ModalBuscarFivemIdAvaliacao(
    LoggingModalMixin, discord.ui.Modal, title="ID FiveM do atendente"
):
    campo_id = discord.ui.TextInput(
        label="ID FiveM",
        placeholder="Ex: 1861",
        required=True,
        max_length=20,
    )

    def __init__(
        self,
        *,
        nota: int | None,
        membro_id_atual: int | None,
        nome_membro_atual: str | None,
    ):
        super().__init__()
        self.nota = nota
        self.membro_id_atual = membro_id_atual
        self.nome_membro_atual = nome_membro_atual

    async def on_submit(self, interacao: discord.Interaction):
        texto = (self.campo_id.value or "").strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números."],
            )
            return

        discord_id = await resolver_discord_id_por_fivem(texto)
        if discord_id is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[f"Nenhum membro com FiveM `{texto}` no banco."],
            )
            return

        membro = None
        if interacao.guild is not None:
            membro = interacao.guild.get_member(discord_id)

        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=[
                    f"Encontrei o Discord `{discord_id}`, "
                    "mas essa pessoa não está no servidor agora."
                ],
            )
            return

        membro = await validar_membro_avaliavel(interacao, membro)
        if membro is None:
            return

        view = ViewFormularioAvaliacao(
            membro_id=membro.id,
            nota=self.nota,
            nome_membro=membro.display_name,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)


# ---------------------------------------------------------------------------
# Comentário + rascunho + confirmação
# ---------------------------------------------------------------------------


class ModalComentarioAvaliacao(
    LoggingModalMixin, discord.ui.Modal, title="Comentário da avaliação"
):
    def __init__(self, *, membro_id: int, nota: int, nome_membro: str):
        super().__init__()
        self.membro_id = membro_id
        self.nota = nota
        self.nome_membro = nome_membro

        if nota <= 2:
            rotulo = "Comentário (obrigatório)"
            placeholder = (
                f"Notas 1 e 2 pedem pelo menos "
                f"{TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA} caracteres."
            )
            obrigatorio = True
        else:
            rotulo = "Mensagem (opcional)"
            placeholder = "Como foi o atendimento? Pode deixar em branco."
            obrigatorio = False

        self.campo_comentario = discord.ui.TextInput(
            label=rotulo,
            style=discord.TextStyle.paragraph,
            placeholder=placeholder,
            required=obrigatorio,
            max_length=500,
            min_length=(
                TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA if nota <= 2 else 0
            ),
        )
        self.add_item(self.campo_comentario)

    async def on_submit(self, interacao: discord.Interaction):
        comentario = (self.campo_comentario.value or "").strip()

        if self.nota <= 2 and len(comentario) < TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA:
            await responder_aviso(
                interacao,
                titulo="Comentário curto demais",
                linhas=[
                    f"Notas **1** e **2** precisam de comentário com pelo "
                    f"menos **{TAMANHO_MINIMO_COMENTARIO_NOTA_BAIXA}** caracteres.",
                ],
            )
            return

        membro = None
        if interacao.guild is not None:
            membro = interacao.guild.get_member(self.membro_id)
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Membro sumiu",
                linhas=["O atendente não está mais no servidor."],
            )
            return

        view = ViewRascunhoAvaliacao(
            membro_id=membro.id,
            nota=self.nota,
            comentario=comentario,
            nome_membro=membro.display_name,
            avaliador_id=interacao.user.id,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)


class ViewRascunhoAvaliacao(LoggingViewMixin, discord.ui.LayoutView):
    """Prévia da avaliação antes de publicar no mural."""

    def __init__(
        self,
        *,
        membro_id: int,
        nota: int,
        comentario: str,
        nome_membro: str,
        avaliador_id: int,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro_id
        self.nota = nota
        self.comentario = comentario
        self.nome_membro = nome_membro
        self.avaliador_id = avaliador_id

        corpo = (
            f"`👤` * **Avaliador:** <@{avaliador_id}>\n"
            f"`⚕️` * **Para:** <@{membro_id}>\n"
            f"`⭐` * **Nota:** {montar_estrelas(nota)} · **{nota}/5**\n"
        )
        if comentario:
            corpo += f"> ```{comentario}```"
        else:
            corpo += "> _Sem comentário._"

        linha_acao = discord.ui.ActionRow()
        botao_confirmar = discord.ui.Button(
            label="Confirmar",
            style=discord.ButtonStyle.success,
            emoji="✅",
        )
        botao_confirmar.callback = self._ao_confirmar
        botao_editar = discord.ui.Button(
            label="Editar",
            style=discord.ButtonStyle.primary,
            emoji="✏️",
        )
        botao_editar.callback = self._ao_editar
        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
        )
        botao_cancelar.callback = self._ao_cancelar
        linha_acao.add_item(botao_confirmar)
        linha_acao.add_item(botao_editar)
        linha_acao.add_item(botao_cancelar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 📋 Rascunho da avaliação"),
                discord.ui.TextDisplay(
                    "Confira como vai ficar no mural. "
                    "Confirme para publicar ou edite se quiser mudar."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(corpo),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_acao,
                accent_color=discord.Color.gold(),
            )
        )

    async def _ao_confirmar(self, interacao: discord.Interaction):
        if interacao.user.id != self.avaliador_id:
            await responder_erro(
                interacao,
                titulo="Não é sua avaliação",
                linhas=["Só quem começou o fluxo pode confirmar."],
            )
            return

        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este fluxo dentro do servidor."],
            )
            return

        membro = interacao.guild.get_member(self.membro_id)
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Membro sumiu",
                linhas=["O atendente não está mais no servidor."],
            )
            return

        if not membro_esta_na_hierarquia(membro):
            await responder_aviso(
                interacao,
                titulo="Fora da hierarquia",
                linhas=[
                    "Esse membro não está mais na hierarquia. "
                    "A avaliação não foi publicada."
                ],
            )
            return

        canal_id = CANAIS.get("MURAL_AVALIACOES") or 0
        canal = interacao.guild.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    "O canal `MURAL_AVALIACOES` não foi encontrado.",
                    "Peça à diretoria para conferir o config.",
                ],
            )
            return

        view_publica = montar_view_publica(
            interacao.user,
            membro,
            self.nota,
            self.comentario,
        )

        try:
            mensagem_mural = await canal.send(view=view_publica)
        except discord.HTTPException as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha ao postar avaliação de atendimento",
                erro,
                contexto="avaliacao_atendimento.mural.send",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Não foi possível registrar",
                linhas=["Falha ao enviar no mural de avaliações. Tente de novo."],
            )
            return

        url_mural = mensagem_mural.jump_url if mensagem_mural else None
        botoes_link = []
        if url_mural:
            botoes_link.append(("Ver no mural", url_mural))

        linhas_dm = [
            f"**Avaliador:** {interacao.user.mention}",
            f"**Nota:** {montar_estrelas(self.nota)} · **{self.nota}/5**",
        ]
        if self.comentario:
            linhas_dm.append(f"**Comentário:** {self.comentario}")
        else:
            linhas_dm.append("**Comentário:** _sem comentário_")

        await enviar_dm_card(
            membro,
            titulo="Você recebeu uma avaliação de atendimento",
            linhas=linhas_dm,
            cor=COR_INFO,
            botoes_link=botoes_link or None,
            guilda=interacao.guild,
        )

        await editar_mensagem_original(
            interacao,
            titulo="Avaliação enviada",
            linhas=[
                f"**Para:** {membro.mention}",
                f"**Nota:** {montar_estrelas(self.nota)} ({self.nota}/5)",
                f"Publicada em <#{canal.id}>.",
                "O profissional foi avisado na DM (se estiver aberta).",
            ],
            texto=None,
        )

    async def _ao_editar(self, interacao: discord.Interaction):
        if interacao.user.id != self.avaliador_id:
            await responder_erro(
                interacao,
                titulo="Não é sua avaliação",
                linhas=["Só quem começou o fluxo pode editar."],
            )
            return

        view = ViewFormularioAvaliacao(
            membro_id=self.membro_id,
            nota=self.nota,
            nome_membro=self.nome_membro,
        )
        await editar_mensagem_original(interacao, view=view, texto=None)

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if interacao.user.id != self.avaliador_id:
            await responder_erro(
                interacao,
                titulo="Não é sua avaliação",
                linhas=["Só quem começou o fluxo pode cancelar."],
            )
            return

        await editar_mensagem_original(
            interacao,
            titulo="Avaliação cancelada",
            linhas=["Nada foi enviado."],
            texto=None,
        )


async def iniciar_fluxo_avaliacao(interacao: discord.Interaction) -> None:
    """Abre o formulário interativo de avaliação de atendimento."""
    if interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use este comando dentro do servidor."],
        )
        return

    await responder_view(
        interacao,
        ViewFormularioAvaliacao(),
        ephemeral=True,
    )
