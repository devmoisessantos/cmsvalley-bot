"""Views e botões dos casos de baú."""

from __future__ import annotations

import discord

from src.bau.bau_service import resolver_caso
from src.config import CANAIS, PRAZO_DEVOLUCAO_BAU_MINUTOS
from src.database.connection import async_session
from src.database.models import CasoBau
from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.utils.mensagens import responder_erro, responder_sucesso, responder_aviso


class ViewCasoBau(LoggingViewMixin, discord.ui.LayoutView):
    """Card persistente no CANAL_ALERTA_BAU."""

    def __init__(self, caso_id: int):
        super().__init__(timeout=None)
        self.caso_id = caso_id

    @staticmethod
    def montar_layout_alerta(
        caso: CasoBau,
        *,
        guild: discord.Guild,
        limite_1: int,
        limite_2: int | None,
    ) -> discord.ui.LayoutView:
        cor = discord.Color.red() if caso.e_grave or caso.status == "PUNIDO" else discord.Color.orange()
        mencao = f"<@{caso.discord_id}>" if caso.discord_id else "_Discord não vinculado_"
        gravidade = "🔴 GRAVE (camada 2)" if caso.e_grave else "🟠 Limite diário (camada 1)"
        texto = (
            f"# 📦 REGISTRO DE CAIXA — BAÚ\n"
            f"> **{gravidade}**\n\n"
            f"> **🆔 FiveM ID:** `{caso.id_fivem}`\n"
            f"> **📡 Membro:** {mencao}\n"
            f"> **👤 Nome na Cidade:** `{caso.nome_cidade or '—'}`\n\n"
            f"## 🍱 ITEM\n"
            f"```yaml\n+ ITEM: x{caso.quantidade_atual} {caso.item_canonico}\n```\n"
            f"Limite 1: **{limite_1}**"
            + (f" · Limite 2: **{limite_2}**" if limite_2 else "")
            + f"\nPrazo: **{PRAZO_DEVOLUCAO_BAU_MINUTOS} min** · Caso `#{caso.id}` · `{caso.status}`"
        )
        if caso.dm_falhou:
            texto += "\n\n⚠️ **DM falhou** — avisar o membro no servidor."

        view = ViewCasoBau(caso_id=caso.id)

        linha = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Devolveu",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"bau:devolveu:{caso.id}",
        )
        b_ok.callback = view._ao_devolveu
        linha.add_item(b_ok)

        b_ocor = discord.ui.Button(
            label="Abrir Ocorrência",
            style=discord.ButtonStyle.danger,
            emoji="📌",
            custom_id=f"bau:ocorrencia:{caso.id}",
        )
        b_ocor.callback = view._ao_ocorrencia
        linha.add_item(b_ocor)

        b_ign = discord.ui.Button(
            label="Justificar/Ignorar",
            style=discord.ButtonStyle.secondary,
            emoji="🔕",
            custom_id=f"bau:ignorar:{caso.id}",
        )
        b_ign.callback = view._ao_ignorar
        linha.add_item(b_ign)

        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=cor,
            )
        )
        return view

    async def _ao_devolveu(self, interacao: discord.Interaction):
        caso = await resolver_caso(
            self.caso_id,
            por_discord_id=interacao.user.id,
            status="RESOLVIDO",
        )
        if caso is None:
            await responder_erro(interacao, titulo="Caso", linhas=["Caso não encontrado."])
            return
        await responder_sucesso(
            interacao,
            titulo="Caso resolvido",
            linhas=[f"Caso `#{self.caso_id}` marcado como **devolvido**."],
        )
        try:
            await interacao.message.edit(
                view=ViewCasoBau.montar_layout_alerta(
                    caso,
                    guild=interacao.guild,
                    limite_1=0,
                    limite_2=None,
                )
            )
        except discord.HTTPException:
            pass

    async def _ao_ocorrencia(self, interacao: discord.Interaction):
        await responder_aviso(
            interacao,
            titulo="Ocorrência",
            linhas=[
                f"Formalize a punição do caso `#{self.caso_id}` pelo painel de **Punições** "
                "(ADV / exoneração conforme gravidade).",
                "O caso permanece aberto até alguém marcar **Devolveu** ou **Justificar**.",
            ],
        )

    async def _ao_ignorar(self, interacao: discord.Interaction):
        await interacao.response.send_modal(ModalJustificarCaso(caso_id=self.caso_id))


class ModalJustificarCaso(LoggingModalMixin, discord.ui.Modal, title="🔕 Justificar / Ignorar"):
    motivo = discord.ui.TextInput(
        label="Motivo obrigatório",
        style=discord.TextStyle.paragraph,
        placeholder="Ex.: autorização da diretoria para evento…",
        required=True,
        max_length=500,
    )

    def __init__(self, caso_id: int):
        super().__init__()
        self.caso_id = caso_id

    async def on_submit(self, interacao: discord.Interaction):
        caso = await resolver_caso(
            self.caso_id,
            por_discord_id=interacao.user.id,
            status="IGNORADO",
            motivo_ignore=self.motivo.value.strip(),
        )
        if caso is None:
            await responder_erro(interacao, titulo="Caso", linhas=["Caso não encontrado."])
            return
        await responder_sucesso(
            interacao,
            titulo="Caso ignorado",
            linhas=[f"Caso `#{self.caso_id}` ignorado com justificativa registrada."],
        )


class ViewDmDevolucao(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, caso_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.caso_id = caso_id
        canal_tickets = CANAIS.get("CANAL_TICKETS_BAU") or 0
        url = (
            f"https://discord.com/channels/{guild_id}/{canal_tickets}"
            if canal_tickets
            else None
        )
        texto = (
            f"# 📦 Aviso do Baú — CMS Valley\n\n"
            f"Você excedeu o limite diário de retiradas do baú.\n"
            f"Devolva o excesso em até **{PRAZO_DEVOLUCAO_BAU_MINUTOS} minutos** "
            f"abrindo um ticket e iniciando a devolução.\n"
            f"Caso `#{caso_id}`."
        )
        linha = discord.ui.ActionRow()
        if url:
            botao = discord.ui.Button(
                label="Iniciar Devolução",
                style=discord.ButtonStyle.link,
                url=url,
                emoji="📥",
            )
            linha.add_item(botao)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto),
                linha if url else discord.ui.TextDisplay("-# Configure CANAL_TICKETS_BAU no config."),
                accent_color=discord.Color.orange(),
            )
        )
