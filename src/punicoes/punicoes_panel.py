"""Painéis Components V2 do sistema de punições."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.database.conexao import async_session
from src.database.models import Punicao
from src.punicoes.punicoes_classes import (
    limpar_sessao,
    obter_sessao,
)
from src.punicoes.punicoes_helpers import (
    e_staff_punicao,
    lista_cargos_punicao_ordenada,
    mensagem_sem_permissao,
    nivel_da_advertencia,
    proximo_cargo_punicao,
    resolver_id_fivem,
    valor_pagamento_advertencia,
)
from src.punicoes.punicoes_logger import registrar_pagamento_advertencia
from src.punicoes.punicoes_service import (
    aplicar_punicao,
    listar_punicoes_membro,
    remover_punicao,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)

registrador = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# PAINEL PRINCIPAL (persistente)
# ═══════════════════════════════════════════════════════════════════════════


class PainelPunicoesLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        row1 = discord.ui.ActionRow()
        b_aplicar = discord.ui.Button(
            label="Aplicar Advertência",
            style=discord.ButtonStyle.danger,
            emoji="⚠️",
            custom_id="punicao:aplicar",
        )
        b_aplicar.callback = self._ao_aplicar
        row1.add_item(b_aplicar)

        b_remover = discord.ui.Button(
            label="Remover Punição",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id="punicao:remover",
        )
        b_remover.callback = self._ao_remover
        row1.add_item(b_remover)

        row2 = discord.ui.ActionRow()
        b_consultar = discord.ui.Button(
            label="Consultar Punição",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id="punicao:consultar",
        )
        b_consultar.callback = self._ao_consultar
        row2.add_item(b_consultar)

        icon_url = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            discord.ui.Section(
                "# 🔨 Painel de Advertência",
                (
                    "-# Use as opções abaixo para aplicar advertência em algum "
                    "membro!\n"
                    "-# Cada advertência é registrada em log portanto evite abusar!\n"
                    "-# Caso tenha dúvidas entre em contato com os Gerais!"
                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "## > **📌 Configurações Ativas:**\n"
                "### ➜ **Aplicação de Advertência:** `✅ Configurada`"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row1,
            row2,
            accent_color=discord.Color.dark_red(),
        )
        self.add_item(self.container)

    async def _ao_aplicar(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao(),
                ],
            )
            return
        limpar_sessao(interaction.user.id)
        obter_sessao(interaction.user.id)
        await responder_view(
            interaction,
            FluxoAplicarAdvertenciaView(interaction.user.id),
            ephemeral=True,
        )

    async def _ao_remover(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao(),
                ],
            )
            return
        await responder_view(
            interaction,
            FluxoRemoverPunicaoView(),
            ephemeral=True,
        )

    async def _ao_consultar(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao(),
                ],
            )
            return
        await responder_view(
            interaction,
            FluxoConsultarPunicaoView(),
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# FLUXO APLICAR (ephemeral, timeout=None nos botões via view 600s)
# ═══════════════════════════════════════════════════════════════════════════


class FluxoAplicarAdvertenciaView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, executor_id: int):
        super().__init__(timeout=600)
        self.executor_id = executor_id
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        sessao = obter_sessao(self.executor_id)

        user_txt = (
            f"{sessao.membro_mention} (`{sessao.membro_id}`)"
            if sessao.membro_id
            else "*Pendente*"
        )
        fivem_txt = f"`{sessao.id_fivem}`" if sessao.id_fivem else "*Pendente*"
        st1 = "✅ Concluída" if sessao.etapa1_ok else "*Pendente*"
        st2 = "✅ Concluída" if sessao.etapa2_ok else "*Pendente*"

        # Etapa 1
        row1 = discord.ui.ActionRow()
        sel = discord.ui.UserSelect(
            placeholder="Selecione o membro no Discord…",
            min_values=1,
            max_values=1,
        )
        sel.callback = self._on_user_select
        row1.add_item(sel)

        row1b = discord.ui.ActionRow()
        b_id = discord.ui.Button(
            label="Buscar membro pelo Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        b_id.callback = self._on_buscar_id
        row1b.add_item(b_id)

        # Etapa 2
        row2 = discord.ui.ActionRow()
        b_fivem = discord.ui.Button(
            label="Vincular ID",
            style=discord.ButtonStyle.primary,
            emoji="🪪",
            disabled=not sessao.etapa1_ok,
        )
        b_fivem.callback = self._on_vincular_fivem
        row2.add_item(b_fivem)

        # Etapa 3 — select do nível + aplicar
        opcoes = [
            discord.SelectOption(label=nome.strip(), value=str(id_do_cargo), emoji="🚫")
            for nome, id_do_cargo in lista_cargos_punicao_ordenada()
        ]
        row3s = discord.ui.ActionRow()
        select_cargo = discord.ui.Select(
            placeholder="Escolha o nível da advertência…",
            options=opcoes,
            disabled=not sessao.pode_aplicar,
        )
        select_cargo.callback = self._on_cargo
        row3s.add_item(select_cargo)

        row3 = discord.ui.ActionRow()
        b_aplicar = discord.ui.Button(
            label="APLICAR ADVERTÊNCIA",
            style=discord.ButtonStyle.danger,
            emoji="⚠️",
            disabled=not (sessao.pode_aplicar and sessao.cargo_id),
        )
        b_aplicar.callback = self._on_aplicar
        row3.add_item(b_aplicar)

        cargo_sel = sessao.cargo_nome or "*Não escolhido*"

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# ⚠️ Aplicar Advertência"),
            discord.ui.TextDisplay(
                "### Informações do usuário:\n"
                f"> - **Usuário selecionado:** {user_txt}\n"
                f"> - **ID no FiveM:** {fivem_txt}\n"
                f"> - **Nível escolhido:** {cargo_sel}\n\n"
                "**OBS:** O botão **APLICAR ADVERTÊNCIA** será liberado após "
                "configurar as etapas abaixo."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                f"⏳ **Etapa 1: escolha o membro**\nStatus atual: {st1}"
            ),
            row1,
            row1b,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                f"⏳ **Etapa 2: adicionar ID do FiveM**\nStatus atual: {st2}"
            ),
            row2,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "⏳ **Etapa 3: aplicar advertência**\n"
                "Escolha o nível e confirme. Só habilita com etapas 1 e 2 concluídas."
            ),
            row3s,
            row3,
            accent_color=discord.Color.orange(),
        )
        self.add_item(self.container)

    async def _refresh(self, interaction: discord.Interaction):
        self._rebuild()
        await editar_mensagem_original(
            interaction,
            view=self,
        )

    async def _on_user_select(self, interaction: discord.Interaction):
        uid = int(interaction.data["values"][0])
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado.",
                ],
            )
            return
        sessao = obter_sessao(self.executor_id)
        sessao.membro_id = membro.id
        sessao.membro_mention = membro.mention
        # tenta preencher FiveM automaticamente
        fivem = await resolver_id_fivem(membro.id)
        if fivem:
            sessao.id_fivem = fivem
        # sugere próximo nível
        proximo = proximo_cargo_punicao(membro)
        if proximo:
            sessao.cargo_nome, sessao.cargo_id = proximo
        await self._refresh(interaction)

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdAdvertencia(self))

    async def _on_vincular_fivem(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalIdFivemAdvertencia(self))

    async def _on_cargo(self, interaction: discord.Interaction):
        id_do_cargo = int(interaction.data["values"][0])
        sessao = obter_sessao(self.executor_id)
        for nome, cid in lista_cargos_punicao_ordenada():
            if cid == id_do_cargo:
                sessao.cargo_nome = nome
                sessao.cargo_id = cid
                break
        await self._refresh(interaction)

    async def _on_aplicar(self, interaction: discord.Interaction):
        sessao = obter_sessao(self.executor_id)
        if not sessao.pode_aplicar or not sessao.cargo_id:
            await responder_aviso(
                interaction,
                titulo="Etapas pendentes",
                linhas=[
                    "Complete as etapas 1 e 2 e escolha o nível.",
                ],
            )
            return
        await interaction.response.send_modal(
            ModalMotivoAdvertencia(
                executor_id=self.executor_id,
                membro_id=sessao.membro_id,
                id_fivem=sessao.id_fivem,
                cargo_nome=sessao.cargo_nome,
                cargo_id=sessao.cargo_id,
            )
        )


class ModalDiscordIdAdvertencia(
    LoggingModalMixin, discord.ui.Modal, title="Buscar Discord ID"
):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 1045831331294220309",
        required=True,
        max_length=20,
    )

    def __init__(self, parent: FluxoAplicarAdvertenciaView):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        """Localiza o membro pelo ID e preenche a sessão de advertência.

        Também tenta recuperar o FiveM e sugere o próximo cargo para reduzir erros de
        digitação. O painel é reconstruído na mesma resposta para deixar claro quais
        etapas foram concluídas antes de uma punição poder ser aplicada.
        """
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interaction,
                titulo="Dado inválido",
                linhas=[
                    "ID inválido.",
                ],
            )
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        sessao = obter_sessao(self.parent.executor_id)
        sessao.membro_id = membro.id
        sessao.membro_mention = membro.mention
        fivem = await resolver_id_fivem(membro.id)
        if fivem:
            sessao.id_fivem = fivem
        proximo = proximo_cargo_punicao(membro)
        if proximo:
            sessao.cargo_nome, sessao.cargo_id = proximo
        self.parent._rebuild()
        await editar_mensagem_original(
            interaction,
            view=self.parent,
        )


class ModalIdFivemAdvertencia(
    LoggingModalMixin, discord.ui.Modal, title="Vincular ID FiveM"
):
    id_fivem = discord.ui.TextInput(
        label="ID FiveM",
        placeholder="Ex: 107891",
        required=True,
        max_length=6,
    )

    def __init__(self, parent: FluxoAplicarAdvertenciaView):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        """Confirma um FiveM numérico na sessão sem aplicá-lo como punição ainda.

        Atualiza a etapa intermediária e redesenha o painel, mantendo a separação entre
        associar a identidade e confirmar a advertência. Isso evita registrar um cargo
        enquanto o operador ainda está revisando os dados do membro.
        """
        valor = self.id_fivem.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interaction,
                titulo="Dado inválido",
                linhas=[
                    "ID inválido.",
                ],
            )
            return
        sessao = obter_sessao(self.parent.executor_id)
        sessao.id_fivem = valor
        self.parent._rebuild()
        await editar_mensagem_original(
            interaction,
            view=self.parent,
        )


class ModalMotivoAdvertencia(
    LoggingModalMixin, discord.ui.Modal, title="⚖️ Aplicar Advertência"
):
    motivo = discord.ui.TextInput(
        label="📄 Motivos — Descreva o motivo",
        style=discord.TextStyle.paragraph,
        placeholder="Explique o motivo da punição...",
        required=True,
        max_length=1500,
    )
    links = discord.ui.TextInput(
        label="🔗 Provas link (medal, youtube, clip…)",
        style=discord.TextStyle.paragraph,
        placeholder="Links de prova (um por linha) — opcional se enviar arquivo",
        required=False,
        max_length=1000,
    )

    def __init__(
        self,
        *,
        executor_id: int,
        membro_id: int,
        id_fivem: str,
        cargo_nome: str,
        cargo_id: int,
    ):
        super().__init__()
        self.executor_id = executor_id
        self.membro_id = membro_id
        self.id_fivem = id_fivem
        self.cargo_nome = cargo_nome
        self.cargo_id = cargo_id
        self.provas_arquivo = None

        # FileUpload (discord.py 2.6+) — dentro de Label, só em Modal
        if hasattr(discord.ui, "FileUpload") and hasattr(discord.ui, "Label"):
            self.provas_arquivo = discord.ui.FileUpload(
                min_values=0,
                max_values=5,
                required=False,
            )
            self.add_item(
                discord.ui.Label(
                    text="📎 Provas em arquivo",
                    description="Imagens, prints, PDF… (até 5 arquivos)",
                    component=self.provas_arquivo,
                )
            )

    async def _coletar_arquivos_prova(self) -> list[tuple[bytes, str]]:
        """Lê os arquivos do FileUpload e devolve [(bytes, nome), ...]."""
        arquivos: list[tuple[bytes, str]] = []
        if self.provas_arquivo is None:
            return arquivos

        anexos = getattr(self.provas_arquivo, "values", None) or []
        for anexo in anexos:
            try:
                dados = await anexo.read()
            except Exception as erro:
                registrador.warning(
                    f"⚠️ [punicoes] falha ao ler arquivo de prova: {erro}"
                )
                continue
            if not dados:
                continue
            nome = getattr(anexo, "filename", None) or "prova.bin"
            arquivos.append((dados, nome))
        return arquivos

    async def on_submit(self, interaction: discord.Interaction):
        """Coleta provas e encaminha a aplicação definitiva para o serviço disciplinar.

        Valida o contexto do executor e do alvo, lê anexos opcionais e então chama a
        regra que grava a punição, altera cargos e publica registros. A sessão
        temporária
        é descartada após a tentativa para impedir que uma confirmação seja repetida.
        """
        if not isinstance(interaction.user, discord.Member):
            await responder_erro(
                interaction,
                titulo="Erro de contexto",
                linhas=[
                    "Erro de contexto.",
                ],
            )
            return
        alvo = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if alvo is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado no servidor.",
                ],
            )
            return

        await interaction.response.defer(ephemeral=True)
        arquivos_provas = await self._coletar_arquivos_prova()

        ok, mensagem, _reg = await aplicar_punicao(
            guild=interaction.guild,
            alvo=alvo,
            executor=interaction.user,
            id_fivem=self.id_fivem,
            cargo_nome=self.cargo_nome,
            cargo_id=self.cargo_id,
            motivo=self.motivo.value.strip(),
            links_texto=self.links.value.strip() if self.links.value else None,
            arquivos_provas=arquivos_provas or None,
        )
        limpar_sessao(self.executor_id)

        view = discord.ui.LayoutView(timeout=120)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# {'✅' if ok else '❌'} Resultado\n{mensagem}"
                ),
                accent_color=discord.Color.green() if ok else discord.Color.red(),
            )
        )
        await responder_view(
            interaction,
            view,
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# REMOVER / CONSULTAR
# ═══════════════════════════════════════════════════════════════════════════


def _fmt_membro_info(membro: discord.Member) -> str:
    return (
        f"- **Discord:** {membro.mention} (`{membro.id}`)\n"
        f"- **Nome atual:** `{membro.display_name}`\n"
        f"- **Conta:** `{membro.name}`"
    )


async def _montar_view_escolher_adv(
    membro: discord.Member,
) -> discord.ui.LayoutView | None:
    """View com Select das advertências ativas. Retorna None se não houver ativas."""
    ativas = await listar_punicoes_membro(membro.id, apenas_ativas=True)
    if not ativas:
        return None

    opcoes = []
    for punicao in ativas[:25]:
        marca_de_tempo = (
            f"<t:{int(punicao.criada_em.timestamp())}:d>" if punicao.criada_em else "—"
        )
        label = f"#{punicao.id} · {punicao.cargo_nome.strip()}"[:100]
        desc = f"{marca_de_tempo} · {(punicao.motivo or '')[:80]}"[:100]
        opcoes.append(
            discord.SelectOption(
                label=label,
                value=str(punicao.id),
                description=desc,
                emoji="⚠️",
            )
        )

    view = EscolherAdvertenciaRemoverView(membro=membro, opcoes=opcoes, ativas=ativas)
    return view


class FluxoRemoverPunicaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    UserSelect → se achar, abre select de advs ativas + motivo.
    Buscar por ID → edita ephemeral com encontrado + Confirmar → select de advs.
    """

    def __init__(
        self,
        membro_id: int | None = None,
        *,
        membro: discord.Member | None = None,
        exigir_confirmacao: bool = False,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro.id if membro else membro_id
        self._membro_cache = membro
        self.exigir_confirmacao = exigir_confirmacao
        self._rebuild()

    def _rebuild(self):
        self.clear_items()

        row_sel = discord.ui.ActionRow()
        sel = discord.ui.UserSelect(
            placeholder="Selecione o usuário…",
            min_values=1,
            max_values=1,
        )
        sel.callback = self._on_user_select
        row_sel.add_item(sel)

        row_btn = discord.ui.ActionRow()
        b_id = discord.ui.Button(
            label="Buscar usuário pelo Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        b_id.callback = self._on_buscar_id
        row_btn.add_item(b_id)

        user_txt = (
            f"<@{self.membro_id}> (`{self.membro_id}`)"
            if self.membro_id
            else "*Nenhum selecionado*"
        )

        items: list = [
            discord.ui.TextDisplay("# 🧹 Remover Punições"),
            discord.ui.TextDisplay(
                "Selecione o usuário no menu abaixo ou busque pelo Discord ID.\n"
                f"**Usuário atual:** {user_txt}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_sel,
            row_btn,
        ]

        # Só mostra "encontrado + confirmar" no caminho do modal de ID
        if self.membro_id and self.exigir_confirmacao:
            info = (
                _fmt_membro_info(self._membro_cache)
                if self._membro_cache is not None
                else f"- **Discord:** <@{self.membro_id}> (`{self.membro_id}`)"
            )
            row_ok = discord.ui.ActionRow()
            b_ok = discord.ui.Button(
                label="Confirmar usuário",
                style=discord.ButtonStyle.success,
                emoji="✅",
            )
            b_ok.callback = self._on_confirmar
            row_ok.add_item(b_ok)
            items.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(
                        "### ✅ Usuário encontrado\n"
                        f"{info}\n"
                        "Confirme para seguir para a remoção de punições."
                    ),
                    row_ok,
                ]
            )

        self.add_item(
            discord.ui.Container(*items, accent_color=discord.Color.dark_grey())
        )

    async def _abrir_escolha_adv(
        self, interaction: discord.Interaction, membro: discord.Member
    ):
        view = await _montar_view_escolher_adv(membro)
        if view is None:
            await editar_mensagem_original(
                interaction,
                view=_view_sem_adv_ativas(membro),
            )
            return
        await editar_mensagem_original(
            interaction,
            view=view,
        )

    async def _on_user_select(self, interaction: discord.Interaction):
        uid = int(interaction.data["values"][0])
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado no servidor.",
                ],
            )
            return
        # Select direto → vai para escolha das advertências ativas
        await self._abrir_escolha_adv(interaction, membro)

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdRemover())

    async def _on_confirmar(self, interaction: discord.Interaction):
        if not self.membro_id:
            await responder_aviso(
                interaction,
                titulo="Nada para mostrar",
                linhas=[
                    "Nenhum usuário selecionado.",
                ],
            )
            return
        membro = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        await self._abrir_escolha_adv(interaction, membro)


def _view_sem_adv_ativas(membro: discord.Member) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=120)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# 🧹 Remover Punições\n"
                f"{membro.mention} (`{membro.id}`)\n\n"
                "❌ Este membro **não possui advertências ativas** no banco."
            ),
            accent_color=discord.Color.red(),
        )
    )
    return view


class EscolherAdvertenciaRemoverView(LoggingViewMixin, discord.ui.LayoutView):
    """Select das advertências ativas → modal de motivo → remove."""

    def __init__(
        self,
        *,
        membro: discord.Member,
        opcoes: list[discord.SelectOption],
        ativas: list,
    ):
        super().__init__(timeout=300)
        self.membro = membro
        self.ativas = ativas

        row = discord.ui.ActionRow()
        sel = discord.ui.Select(
            placeholder="Escolha a advertência ativa para remover…",
            options=opcoes,
            min_values=1,
            max_values=1,
        )
        sel.callback = self._on_select_adv
        row.add_item(sel)

        lista_de_texto = "\n".join(
            f"• `#{punicao.id}` **{punicao.cargo_nome.strip()}** — "
            f"{(punicao.motivo or '')[:80]}"
            for punicao in ativas[:10]
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧹 Remover Punições"),
                discord.ui.TextDisplay(
                    f"**Usuário:** {membro.mention} (`{membro.id}`)\n"
                    f"{_fmt_membro_info(membro)}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    f"### ⚠️ Advertências ativas ({len(ativas)})\n{lista_de_texto}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "Selecione abaixo qual advertência deseja remover:"
                ),
                row,
                accent_color=discord.Color.dark_grey(),
            )
        )

    async def _on_select_adv(self, interaction: discord.Interaction):
        punicao_id = int(interaction.data["values"][0])
        cargo_nome = ""
        for punicao in self.ativas:
            if int(punicao.id) == punicao_id:
                cargo_nome = punicao.cargo_nome or ""
                break
        await interaction.response.edit_message(
            view=EscolherTipoRemocaoView(
                membro=self.membro,
                punicao_id=punicao_id,
                cargo_nome=cargo_nome,
            )
        )


class EscolherTipoRemocaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Após escolher a advertência: pagar (com comprovante) ou
    revogar sem pagamento.
    """

    def __init__(
        self,
        *,
        membro: discord.Member,
        punicao_id: int,
        cargo_nome: str = "",
    ):
        super().__init__(timeout=300)
        self.membro = membro
        self.punicao_id = punicao_id
        self.cargo_nome = cargo_nome or ""

        from src.utils.formatacao import formatar_reais

        valor, rotulo = valor_pagamento_advertencia(
            self.cargo_nome,
            membro=membro,
        )
        if valor > 0:
            texto_valor = (
                f"**Valor a pagar:** `{formatar_reais(valor)}`\n"
                f"**Referência:** {rotulo}\n\n"
            )
        elif nivel_da_advertencia(self.cargo_nome) == 0:
            texto_valor = (
                "**ADV VERBAL** — sem valor formal de pagamento; "
                "não usa desconto em moedas.\n\n"
            )
        else:
            texto_valor = ""

        linha = discord.ui.ActionRow()
        botao_pagar = discord.ui.Button(
            label="Pagar e remover",
            style=discord.ButtonStyle.success,
        )
        botao_pagar.callback = self._ao_pagar
        linha.add_item(botao_pagar)

        botao_revogar = discord.ui.Button(
            label="Revogar (sem pagamento)",
            style=discord.ButtonStyle.secondary,
        )
        botao_revogar.callback = self._ao_revogar
        linha.add_item(botao_revogar)

        nome_punicao = (self.cargo_nome or "—").strip()
        corpo = (
            f"**Membro:** {membro.mention}\n"
            f"**Registro:** `#{punicao_id}`\n"
            f"**Punição:** **{nome_punicao}**\n\n"
            f"{texto_valor}"
            "**Pagar e remover** — comprovante in-game; até 10 "
            "moedas de desconto em Adv formal (Verbal não usa "
            "moedas).\n"
            "**Revogar** — remove sem pagamento (decisão da "
            "equipe; motivo obrigatório)."
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧹 Como remover esta punição?"),
                discord.ui.TextDisplay(corpo),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=discord.Color.dark_grey(),
            )
        )

    async def _ao_pagar(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ModalMotivoRemocao(
                alvo=self.membro,
                punicao_id=self.punicao_id,
                tipo_remocao="PAGAMENTO",
            )
        )

    async def _ao_revogar(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ModalMotivoRemocao(
                alvo=self.membro,
                punicao_id=self.punicao_id,
                tipo_remocao="REVOGACAO",
            )
        )



class ModalDiscordIdRemover(
    LoggingModalMixin, discord.ui.Modal, title="Buscar Discord ID"
):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 1045831331294220309",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Busca o membro para remoção e pede confirmação antes de mostrar punições.

        Exige um identificador numérico presente na guilda e troca a view pela etapa de
        confirmação. Esse passo impede que uma advertência ativa seja removida por
        engano logo após digitar um ID parecido.
        """
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interaction,
                titulo="Dado inválido",
                linhas=[
                    "ID inválido.",
                ],
            )
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        # Edita a ephemeral com usuário encontrado + botão Confirmar
        await editar_mensagem_original(
            interaction,
            view=FluxoRemoverPunicaoView(membro=membro, exigir_confirmacao=True),
        )


class ModalMotivoRemocao(LoggingModalMixin, discord.ui.Modal, title="Remover punição"):
    motivo = discord.ui.TextInput(
        label="Motivo da remoção (opcional)",
        required=False,
        max_length=500,
        placeholder="Ex: Pagamento via Ticket",
    )

    def __init__(
        self,
        alvo: discord.Member,
        punicao_id: int | None = None,
        tipo_remocao: str = "PAGAMENTO",
    ):
        super().__init__()
        self.alvo = alvo
        self.punicao_id = punicao_id
        # PAGAMENTO = exige comprovante | REVOGACAO = sem pagamento
        self.tipo_remocao = (tipo_remocao or "PAGAMENTO").upper()
        self.comprovante_arquivo = None
        self.campo_moedas = None

        # FileUpload e desconto em moedas só no caminho de pagamento
        if self.tipo_remocao == "PAGAMENTO":
            from src.config import MOEDAS_DESCONTO_MAX_POR_PEDIDO

            teto = int(MOEDAS_DESCONTO_MAX_POR_PEDIDO or 10)
            self.campo_moedas = discord.ui.TextInput(
                label=f"Moedas de desconto (0 a {teto})",
                required=False,
                max_length=2,
                placeholder=f"0 — máx. {teto}. Verbal não aceita moedas.",
            )
            self.add_item(self.campo_moedas)

            if hasattr(discord.ui, "FileUpload") and hasattr(discord.ui, "Label"):
                self.comprovante_arquivo = discord.ui.FileUpload(
                    min_values=1,
                    max_values=3,
                    required=True,
                )
                self.add_item(
                    discord.ui.Label(
                        text="📎 Comprovante de pagamento",
                        description="Print/PDF do restante in-game (obrigatório)",
                        component=self.comprovante_arquivo,
                    )
                )

    async def _coletar_comprovantes(self) -> list[tuple[bytes, str]]:
        """Baixa todos os bytes dos anexos antes de qualquer publicação."""
        arquivos: list[tuple[bytes, str]] = []
        if self.comprovante_arquivo is None:
            return arquivos

        anexos = getattr(self.comprovante_arquivo, "values", None) or []
        for anexo in anexos:
            try:
                dados = await anexo.read()
            except Exception as erro:
                registrador.warning(
                    "⚠️ [punicoes] falha ao ler comprovante de pagamento: %s",
                    erro,
                )
                continue
            if not dados:
                continue
            nome = getattr(anexo, "filename", None) or "comprovante_pagamento.png"
            arquivos.append((dados, nome))
        return arquivos

    async def on_submit(self, interaction: discord.Interaction):
        """Remove a punição com comprovante e registra o pagamento no canal.

        Fluxo (igual ao laudo):
        1. Baixa todos os bytes do comprovante
        2. Mostra mensagem de processando
        3. Remove a punição
        4. Publica o pagamento em CANAL_PAGAMENTO_ADV
        5. Só então envia a mensagem de sucesso
        """
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await responder_erro(
                interaction,
                titulo="Erro de contexto",
                linhas=["Este comando só funciona dentro do servidor."],
            )
            return

        # Defer cedo (limite de 3s do Discord); bytes vêm em seguida, antes de publicar
        await interaction.response.defer(ephemeral=True)

        e_pagamento = self.tipo_remocao == "PAGAMENTO"
        texto_processando = (
            "Baixando o comprovante e removendo a punição…"
            if e_pagamento
            else "Revogando a punição…"
        )
        await responder_info(
            interaction,
            titulo="Processando remoção",
            linhas=[texto_processando],
            delay=None,
        )

        motivo_bruto = self.motivo.value.strip() if self.motivo.value else ""
        if not e_pagamento and not motivo_bruto:
            await responder_erro(
                interaction,
                titulo="Motivo obrigatório",
                linhas=[
                    "Na revogação sem pagamento, informe o motivo.",
                ],
                delay=12,
            )
            return

        # 1) Comprovante só no caminho de pagamento
        comprovantes: list[tuple[bytes, str]] = []
        if e_pagamento:
            comprovantes = await self._coletar_comprovantes()
            if not comprovantes:
                await responder_erro(
                    interaction,
                    titulo="Comprovante obrigatório",
                    linhas=[
                        "Anexe o print/PDF do pagamento para remover "
                        "a advertência.",
                    ],
                    delay=12,
                )
                return

        # Dados da punição antes de baixar (cargo, id, fivem)
        cargo_id_adv = 0
        cargo_nome_adv = "Advertência"
        id_fivem: str | None = None
        if self.punicao_id is not None:
            async with async_session() as session:
                resultado = await session.execute(
                    select(Punicao).where(Punicao.id == self.punicao_id)
                )
                registro = resultado.scalar_one_or_none()
                if registro is not None:
                    cargo_id_adv = int(registro.cargo_id)
                    cargo_nome_adv = registro.cargo_nome or cargo_nome_adv
                    id_fivem = registro.id_fivem

        if not id_fivem:
            id_fivem = await resolver_id_fivem(self.alvo.id)

        moedas_usadas = 0
        saldo_apos_moedas: int | None = None
        if e_pagamento:
            texto_moedas = ""
            if self.campo_moedas is not None and self.campo_moedas.value:
                texto_moedas = self.campo_moedas.value.strip()
            if texto_moedas:
                if not texto_moedas.isdigit():
                    await responder_erro(
                        interaction,
                        titulo="Moedas inválidas",
                        linhas=["Informe um número inteiro de moedas."],
                        delay=12,
                    )
                    return
                moedas_usadas = int(texto_moedas)
            from src.config import MOEDAS_DESCONTO_MAX_POR_PEDIDO

            teto_moedas = int(MOEDAS_DESCONTO_MAX_POR_PEDIDO or 10)
            if moedas_usadas < 0 or moedas_usadas > teto_moedas:
                await responder_erro(
                    interaction,
                    titulo="Limite de moedas",
                    linhas=[
                        f"Use de 0 a {teto_moedas} moedas como desconto.",
                    ],
                    delay=12,
                )
                return

            e_verbal = "verbal" in (cargo_nome_adv or "").lower()
            if e_verbal and moedas_usadas > 0:
                await responder_erro(
                    interaction,
                    titulo="Verbal sem desconto em moedas",
                    linhas=[
                        "ADV VERBAL não aceita desconto em moedas. "
                        "Use 0 ou deixe em branco.",
                    ],
                    delay=12,
                )
                return

            if moedas_usadas > 0:
                from src.membros.membros_service import ajustar_saldo_moedas

                try:
                    _antes, saldo_apos_moedas = await ajustar_saldo_moedas(
                        self.alvo.id,
                        delta=-moedas_usadas,
                        executor_id=interaction.user.id,
                        referencia=(
                            f"Desconto pagamento ADV "
                            f"#{self.punicao_id or '—'}"
                        ),
                    )
                except ValueError as erro_saldo:
                    await responder_erro(
                        interaction,
                        titulo="Saldo insuficiente",
                        linhas=[str(erro_saldo)],
                        delay=12,
                    )
                    return
                except Exception as erro_moedas:
                    registrador.exception(
                        "Falha ao debitar moedas na remoção de ADV de %s: %s",
                        self.alvo.id,
                        erro_moedas,
                    )
                    await responder_erro(
                        interaction,
                        titulo="Falha nas moedas",
                        linhas=[
                            "Não consegui debitar as moedas agora. "
                            "Tente de novo.",
                        ],
                        delay=12,
                    )
                    return

            if motivo_bruto:
                motivo_txt = f"PAGAMENTO: {motivo_bruto}"
            else:
                motivo_txt = "PAGAMENTO"
            if moedas_usadas > 0:
                motivo_txt = (
                    f"{motivo_txt} · desconto {moedas_usadas} moeda(s)"
                )
        else:
            motivo_txt = f"REVOGACAO: {motivo_bruto}"

        ok, mensagem = await remover_punicao(
            guild=interaction.guild,
            alvo=self.alvo,
            executor=interaction.user,
            punicao_id=self.punicao_id,
            motivo_remocao=motivo_txt,
        )

        # Apaga a ephemeral do select de punições ativas (não a ficha).
        mensagem_do_painel = interaction.message
        if mensagem_do_painel is not None:
            try:
                await mensagem_do_painel.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if not ok:
            # Devolve moedas se já tinham sido debitadas e a remoção falhou.
            if e_pagamento and moedas_usadas > 0:
                try:
                    from src.membros.membros_service import ajustar_saldo_moedas

                    await ajustar_saldo_moedas(
                        self.alvo.id,
                        delta=moedas_usadas,
                        executor_id=interaction.user.id,
                        referencia=(
                            f"Estorno desconto ADV "
                            f"#{self.punicao_id or '—'} (remoção falhou)"
                        ),
                    )
                except Exception as erro_estorno:
                    registrador.exception(
                        "Falha ao estornar moedas de %s após remoção "
                        "falhar: %s",
                        self.alvo.id,
                        erro_estorno,
                    )
            await responder_erro(
                interaction,
                titulo="Não foi possível remover",
                linhas=[mensagem],
                delay=12,
            )
            return

        # 4) Publica comprovante(s) só no caminho de pagamento
        mensagens_pagamento: list[discord.Message] = []
        if not e_pagamento:
            comprovantes = []
        for bytes_arquivo, nome_arquivo in comprovantes:
            valor_esp, rotulo_esp = valor_pagamento_advertencia(
                cargo_nome_adv,
                membro=self.alvo,
            )
            msg_pag = await registrar_pagamento_advertencia(
                guild=interaction.guild,
                alvo=self.alvo,
                executor=interaction.user,
                cargo_id_advertencia=cargo_id_adv,
                cargo_nome_advertencia=cargo_nome_adv,
                punicao_id=self.punicao_id,
                id_fivem=id_fivem,
                motivo_remocao=motivo_txt,
                bytes_comprovante=bytes_arquivo,
                nome_arquivo=nome_arquivo,
                moedas_desconto=moedas_usadas,
                saldo_moedas_apos=saldo_apos_moedas,
                valor_esperado=valor_esp if valor_esp > 0 else None,
                rotulo_valor=rotulo_esp,
            )
            if msg_pag is not None:
                mensagens_pagamento.append(msg_pag)

        linhas_sucesso = [mensagem]
        if e_pagamento:
            if moedas_usadas > 0:
                linhas_sucesso.append(
                    f"Desconto: **{moedas_usadas}** moeda(s) debitadas "
                    f"do membro"
                    + (
                        f" (saldo agora: **{saldo_apos_moedas}**)."
                        if saldo_apos_moedas is not None
                        else "."
                    )
                )
            if mensagens_pagamento:
                linhas_sucesso.append(
                    f"Comprovante registrado no canal de pagamento "
                    f"({len(mensagens_pagamento)} arquivo(s))."
                )
            else:
                linhas_sucesso.append(
                    "⚠️ Punição removida, mas não consegui publicar o "
                    "comprovante no canal de pagamento. Verifique "
                    "`CANAL_PAGAMENTO_ADV`."
                )
        else:
            linhas_sucesso.append(
                "Revogação registrada (sem pagamento)."
            )

        await responder_sucesso(
            interaction,
            titulo="Punição removida",
            linhas=linhas_sucesso,
            delay=10,
        )


class FluxoConsultarPunicaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    UserSelect → se achar, pode ir ao histórico (ou remove via botão no histórico).
    Buscar por ID → edita ephemeral com encontrado + Confirmar → histórico.
    """

    def __init__(
        self,
        membro_id: int | None = None,
        *,
        membro: discord.Member | None = None,
        exigir_confirmacao: bool = False,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro.id if membro else membro_id
        self._membro_cache = membro
        self.exigir_confirmacao = exigir_confirmacao
        self._rebuild()

    def _rebuild(self):
        self.clear_items()

        row_sel = discord.ui.ActionRow()
        sel = discord.ui.UserSelect(
            placeholder="Selecione o usuário…",
            min_values=1,
            max_values=1,
        )
        sel.callback = self._on_user_select
        row_sel.add_item(sel)

        row_btn = discord.ui.ActionRow()
        b_id = discord.ui.Button(
            label="Buscar usuário pelo Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        b_id.callback = self._on_buscar_id
        row_btn.add_item(b_id)

        user_txt = (
            f"<@{self.membro_id}> (`{self.membro_id}`)"
            if self.membro_id
            else "*Nenhum selecionado*"
        )

        items: list = [
            discord.ui.TextDisplay("# ⚖️ Consultar Punições"),
            discord.ui.TextDisplay(
                "Selecione o usuário no menu abaixo ou busque pelo Discord ID.\n"
                f"**Usuário atual:** {user_txt}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_sel,
            row_btn,
        ]

        if self.membro_id and self.exigir_confirmacao:
            info = (
                _fmt_membro_info(self._membro_cache)
                if self._membro_cache is not None
                else f"- **Discord:** <@{self.membro_id}> (`{self.membro_id}`)"
            )
            row_ok = discord.ui.ActionRow()
            b_ok = discord.ui.Button(
                label="Confirmar usuário",
                style=discord.ButtonStyle.success,
                emoji="✅",
            )
            b_ok.callback = self._on_confirmar
            row_ok.add_item(b_ok)
            items.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(
                        "### ✅ Usuário encontrado\n"
                        f"{info}\n"
                        "Confirme para ver o histórico de punições."
                    ),
                    row_ok,
                ]
            )

        self.add_item(
            discord.ui.Container(*items, accent_color=discord.Color.blurple())
        )

    async def _on_user_select(self, interaction: discord.Interaction):
        uid = int(interaction.data["values"][0])
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado no servidor.",
                ],
            )
            return
        # Select direto → histórico
        await _exibir_historico_punicoes(interaction, membro)

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdConsultar())

    async def _on_confirmar(self, interaction: discord.Interaction):
        if not self.membro_id:
            await responder_aviso(
                interaction,
                titulo="Nada para mostrar",
                linhas=[
                    "Nenhum usuário selecionado.",
                ],
            )
            return
        membro = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        await _exibir_historico_punicoes(interaction, membro)


class ModalDiscordIdConsultar(
    LoggingModalMixin, discord.ui.Modal, title="Buscar Discord ID"
):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 1045831331294220309",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Localiza o membro e abre uma confirmação antes de exibir seu histórico.

        Aceita somente um ID numérico da guilda atual e preserva o passo de conferência
        para evitar revelar ou consultar registros de uma pessoa escolhida por engano.
        """
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interaction,
                titulo="Dado inválido",
                linhas=[
                    "ID inválido.",
                ],
            )
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        await editar_mensagem_original(
            interaction,
            view=FluxoConsultarPunicaoView(membro=membro, exigir_confirmacao=True),
        )


async def _exibir_historico_punicoes(
    interaction: discord.Interaction,
    membro: discord.Member | discord.abc.User | object,
):
    """Monta o painel de histórico e edita a ephemeral."""
    id_do_membro = int(getattr(membro, "id"))
    registros = await listar_punicoes_membro(id_do_membro)
    ativas = [registro for registro in registros if registro.ativa]
    total = len(registros)

    if not registros:
        corpo = "_Nenhuma punição registrada no banco._"
    else:
        linhas = []
        for indice, registro in enumerate(registros[:15], start=1):
            marca_de_tempo = (
                f"<t:{int(registro.criada_em.timestamp())}:F>"
                if registro.criada_em
                else "—"
            )
            if registro.ativa:
                status = "**Status:** Ativa"
            else:
                rem_ts = (
                    f"<t:{int(registro.removida_em.timestamp())}:d>"
                    if registro.removida_em
                    else "—"
                )
                rem_por = (
                    f" - **Removida por:** `({registro.removida_por})`"
                    if registro.removida_por
                    else ""
                )
                status = f"**Status:** Removida em {rem_ts}{rem_por}"
                if registro.motivo_remocao:
                    status += (
                        f"\n- **Motivo da remoção:** {registro.motivo_remocao[:200]}"
                    )

            linhas.append(
                f"**{indice}. {marca_de_tempo}**\n"
                f"- **Staff:** <@{registro.executor_id}> `({registro.executor_id})` "
                f"- **Tipo:** {registro.cargo_nome.strip()}\n"
                f"- {status}\n"
                f"- **Motivo:** {registro.motivo[:300]}"
            )
        corpo = "\n\n".join(linhas)

    row_rm = discord.ui.ActionRow()
    b_rm = discord.ui.Button(
        label="Remover",
        style=discord.ButtonStyle.secondary,
        emoji="🧹",
    )

    async def _ao_ir_remover(interacao: discord.Interaction):
        membro_no_servidor = (
            interacao.guild.get_member(id_do_membro) if interacao.guild else None
        )
        if membro_no_servidor is None:
            await responder_erro(
                interacao,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor.",
                ],
            )
            return
        # Vai direto para o select de advertências ativas
        view = await _montar_view_escolher_adv(membro_no_servidor)
        if view is None:
            await editar_mensagem_original(
                interacao,
                view=_view_sem_adv_ativas(membro_no_servidor),
            )
            return
        await editar_mensagem_original(
            interacao,
            view=view,
        )

    b_rm.callback = _ao_ir_remover
    row_rm.add_item(b_rm)

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# ⚖️ Histórico de Punições\n"
                f"**Usuário:** <@{id_do_membro}> (`{id_do_membro}`)"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                f"### 📊 Resumo\n"
                f"- **Advertências:** {total}\n"
                f"- **Ativas:** {len(ativas)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(f"### ⚠️ Advertências\n\n{corpo}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_rm,
            accent_color=discord.Color.blurple(),
        )
    )
    await editar_mensagem_original(
        interaction,
        view=view,
    )
