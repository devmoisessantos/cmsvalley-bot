"""Painéis Components V2 do sistema de punições."""

from __future__ import annotations

import discord

from src.punicoes.classes import (
    limpar_sessao,
    obter_sessao,
)
from src.punicoes.helpers import (
    e_staff_punicao,
    lista_cargos_punicao_ordenada,
    mensagem_sem_permissao,
    proximo_cargo_punicao,
    resolver_id_fivem,
)
from src.punicoes.services import (
    aplicar_punicao,
    listar_punicoes_membro,
    remover_punicao,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)

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
        b_aplicar.callback = self._cb_aplicar
        row1.add_item(b_aplicar)

        b_remover = discord.ui.Button(
            label="Remover Punição",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id="punicao:remover",
        )
        b_remover.callback = self._cb_remover
        row1.add_item(b_remover)

        row2 = discord.ui.ActionRow()
        b_consultar = discord.ui.Button(
            label="Consultar Punição",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id="punicao:consultar",
        )
        b_consultar.callback = self._cb_consultar
        row2.add_item(b_consultar)

        icon_url = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            discord.ui.Section(
                "# 🔨 Painel de Advertência",
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.TextDisplay(
                "-# Use as opções abaixo para aplicar advertência em algum membro!\n"
                "-# Cada advertência é registrada em log portanto evite abusar!\n"
                "-# Caso tenha dúvidas entre em contato com os Gerais!"
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

    async def _cb_aplicar(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await interaction.response.send_message(
                mensagem_sem_permissao(), ephemeral=True
            )
            return
        limpar_sessao(interaction.user.id)
        obter_sessao(interaction.user.id)
        await interaction.response.send_message(
            view=FluxoAplicarAdvertenciaView(interaction.user.id),
            ephemeral=True,
        )

    async def _cb_remover(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await interaction.response.send_message(
                mensagem_sem_permissao(), ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=FluxoRemoverPunicaoView(),
            ephemeral=True,
        )

    async def _cb_consultar(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not e_staff_punicao(
            interaction.user
        ):
            await interaction.response.send_message(
                mensagem_sem_permissao(), ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=FluxoConsultarPunicaoView(),
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
            discord.SelectOption(label=nome.strip(), value=str(rid), emoji="🚫")
            for nome, rid in lista_cargos_punicao_ordenada()
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
                "**OBS:** O botão **APLICAR ADVERTÊNCIA** será liberado após configurar as etapas abaixo."
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
        await interaction.response.edit_message(view=self)

    async def _on_user_select(self, interaction: discord.Interaction):
        uid = int(interaction.data["values"][0])
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não encontrado.", ephemeral=True
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
        prox = proximo_cargo_punicao(membro)
        if prox:
            sessao.cargo_nome, sessao.cargo_id = prox
        await self._refresh(interaction)

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdAdvertencia(self))

    async def _on_vincular_fivem(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalIdFivemAdvertencia(self))

    async def _on_cargo(self, interaction: discord.Interaction):
        rid = int(interaction.data["values"][0])
        sessao = obter_sessao(self.executor_id)
        for nome, cid in lista_cargos_punicao_ordenada():
            if cid == rid:
                sessao.cargo_nome = nome
                sessao.cargo_id = cid
                break
        await self._refresh(interaction)

    async def _on_aplicar(self, interaction: discord.Interaction):
        sessao = obter_sessao(self.executor_id)
        if not sessao.pode_aplicar or not sessao.cargo_id:
            await interaction.response.send_message(
                "❌ Complete as etapas 1 e 2 e escolha o nível.", ephemeral=True
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
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
            )
            return
        sessao = obter_sessao(self.parent.executor_id)
        sessao.membro_id = membro.id
        sessao.membro_mention = membro.mention
        fivem = await resolver_id_fivem(membro.id)
        if fivem:
            sessao.id_fivem = fivem
        prox = proximo_cargo_punicao(membro)
        if prox:
            sessao.cargo_nome, sessao.cargo_id = prox
        self.parent._rebuild()
        await interaction.response.edit_message(view=self.parent)


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
        valor = self.id_fivem.value.strip()
        if not valor.isdigit():
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        sessao = obter_sessao(self.parent.executor_id)
        sessao.id_fivem = valor
        self.parent._rebuild()
        await interaction.response.edit_message(view=self.parent)


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
        placeholder="Anexe aqui as provas em links (um por linha)",
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

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Erro de contexto.", ephemeral=True
            )
            return
        alvo = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if alvo is None:
            await interaction.response.send_message(
                "❌ Membro não encontrado no servidor.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        ok, msg, _reg = await aplicar_punicao(
            guild=interaction.guild,
            alvo=alvo,
            executor=interaction.user,
            id_fivem=self.id_fivem,
            cargo_nome=self.cargo_nome,
            cargo_id=self.cargo_id,
            motivo=self.motivo.value.strip(),
            links_texto=self.links.value.strip() if self.links.value else None,
        )
        limpar_sessao(self.executor_id)

        view = discord.ui.LayoutView(timeout=120)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# {'✅' if ok else '❌'} Resultado\n{msg}"),
                accent_color=discord.Color.green() if ok else discord.Color.red(),
            )
        )
        await interaction.followup.send(view=view, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════════════
# REMOVER / CONSULTAR
# ═══════════════════════════════════════════════════════════════════════════


def _fmt_membro_info(membro: discord.Member) -> str:
    return (
        f"- **Discord:** {membro.mention} (`{membro.id}`)\n"
        f"- **Nome atual:** `{membro.display_name}`\n"
        f"- **Conta:** `{membro.name}`"
    )


class FluxoRemoverPunicaoView(LoggingViewMixin, discord.ui.LayoutView):
    """Seleciona usuário (UserSelect ou modal por ID) → confirma → motivo → remove."""

    def __init__(
        self,
        membro_id: int | None = None,
        *,
        membro: discord.Member | None = None,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro.id if membro else membro_id
        self._membro_cache = membro  # dados frescos p/ Nome atual / Conta
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

        if self.membro_id:
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

    async def _on_user_select(self, interaction: discord.Interaction):
        uid = int(interaction.data["values"][0])
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não encontrado no servidor.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=FluxoRemoverPunicaoView(membro=membro)
        )

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdRemover())

    async def _on_confirmar(self, interaction: discord.Interaction):
        if not self.membro_id:
            await interaction.response.send_message(
                "❌ Nenhum usuário selecionado.", ephemeral=True
            )
            return
        membro = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ModalMotivoRemocao(membro))


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
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
            )
            return
        # Edita a ephemeral original com usuário encontrado + botão confirmar
        await interaction.response.edit_message(
            view=FluxoRemoverPunicaoView(membro=membro)
        )


class ModalMotivoRemocao(LoggingModalMixin, discord.ui.Modal, title="Remover punição"):
    motivo = discord.ui.TextInput(
        label="Motivo da remoção (opcional)",
        required=False,
        max_length=500,
    )

    def __init__(self, alvo: discord.Member):
        super().__init__()
        self.alvo = alvo

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, msg = await remover_punicao(
            guild=interaction.guild,
            alvo=self.alvo,
            executor=interaction.user,
            motivo_remocao=self.motivo.value.strip() if self.motivo.value else None,
        )
        await interaction.followup.send(msg, ephemeral=True)


class FluxoConsultarPunicaoView(LoggingViewMixin, discord.ui.LayoutView):
    """Seleciona usuário (UserSelect ou modal por ID) → confirma → histórico."""

    def __init__(
        self,
        membro_id: int | None = None,
        *,
        membro: discord.Member | None = None,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro.id if membro else membro_id
        self._membro_cache = membro
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

        if self.membro_id:
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
            await interaction.response.send_message(
                "❌ Membro não encontrado no servidor.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=FluxoConsultarPunicaoView(membro=membro)
        )

    async def _on_buscar_id(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalDiscordIdConsultar())

    async def _on_confirmar(self, interaction: discord.Interaction):
        if not self.membro_id:
            await interaction.response.send_message(
                "❌ Nenhum usuário selecionado.", ephemeral=True
            )
            return
        membro = (
            interaction.guild.get_member(self.membro_id) if interaction.guild else None
        )
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
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
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        uid = int(raw)
        membro = interaction.guild.get_member(uid) if interaction.guild else None
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            view=FluxoConsultarPunicaoView(membro=membro)
        )


async def _exibir_historico_punicoes(
    interaction: discord.Interaction, membro: discord.Member
):
    """Monta o painel de histórico no estilo do mockup e edita a ephemeral."""
    regs = await listar_punicoes_membro(membro.id)
    ativas = [p for p in regs if p.ativa]
    total = len(regs)

    if not regs:
        corpo = "_Nenhuma punição registrada no banco._"
    else:
        linhas = []
        for i, p in enumerate(regs[:15], start=1):
            ts = f"<t:{int(p.criada_em.timestamp())}:F>" if p.criada_em else "—"
            if p.ativa:
                status = "**Status:** Ativa"
            else:
                rem_ts = (
                    f"<t:{int(p.removida_em.timestamp())}:d>" if p.removida_em else "—"
                )
                rem_por = (
                    f" - **Removida por:** `({p.removida_por})`"
                    if p.removida_por
                    else ""
                )
                status = f"**Status:** Removida em {rem_ts}{rem_por}"
                if p.motivo_remocao:
                    status += f"\n- **Motivo da remoção:** {p.motivo_remocao[:200]}"

            linhas.append(
                f"**{i}. {ts}**\n"
                f"- **Staff:** <@{p.executor_id}> `({p.executor_id})` "
                f"- **Tipo:** {p.cargo_nome.strip()}\n"
                f"- {status}\n"
                f"- **Motivo:** {p.motivo[:300]}"
            )
        corpo = "\n\n".join(linhas)

    row_rm = discord.ui.ActionRow()
    b_rm = discord.ui.Button(
        label="Remover",
        style=discord.ButtonStyle.secondary,
        emoji="🧹",
    )

    async def _cb_ir_remover(inter: discord.Interaction):
        m = inter.guild.get_member(membro.id) if inter.guild else None
        if m is None:
            await inter.response.send_message(
                "❌ Membro não está no servidor.", ephemeral=True
            )
            return
        await inter.response.edit_message(view=FluxoRemoverPunicaoView(membro=m))

    b_rm.callback = _cb_ir_remover
    row_rm.add_item(b_rm)

    view = discord.ui.LayoutView(timeout=180)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# ⚖️ Histórico de Punições\n"
                f"**Usuário:** {membro.mention} (`{membro.id}`)"
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
    await interaction.response.edit_message(view=view)
