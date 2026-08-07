# src/gate/gate_cogs.py
"""
Cog único do GATE.

- Listener unificado: botões gate:* e presenca:*
- Grupo slash /gate (listar, status, meus, ajuda)
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from src.gate.gate_logger import atualizar_log_evento
from src.gate.gate_modals import (
    ModalConfirmarPresenca,
    ModalDominas,
    ModalFacXFac,
    ModalTreino,
)
from src.gate.gate_presenca import atualizar_painel_presenca
from src.gate.gate_service import (
    buscar_evento_por_id,
    cancelar_presenca,
    contar_presencas,
    encerrar_evento,
    listar_eventos_abertos,
    listar_presencas,
    listar_presencas_do_membro,
    listar_ultimos_eventos,
    tem_permissao_criar_evento,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    CardView,
    enviar_card,
    excluir_mensagem,
    responder_aviso,
    responder_card,
    responder_erro,
    responder_info,
)


class SelectEncerrarEvento(discord.ui.Select):
    """Select com os eventos abertos para o staff escolher qual encerrar."""

    def __init__(self, eventos: list):
        opcoes = [
            discord.SelectOption(
                label=f"{evento.titulo} — {evento.data_evento} {evento.horario}",
                value=str(evento.id),
                description=f"ID {evento.id} · {evento.tipo}",
            )
            for evento in eventos[:25]
        ]
        super().__init__(
            placeholder="Selecione o evento para encerrar",
            options=opcoes,
        )

    async def callback(self, interacao: discord.Interaction):
        evento_id = int(self.values[0])
        evento = await encerrar_evento(evento_id)

        if evento is None:
            view_do_card = CardView(
                titulo="Evento não encontrado",
                linhas=["O evento selecionado não existe mais ou já estava encerrado."],
                cor=COR_ERRO,
                timeout=None,
            )
        else:
            await atualizar_log_evento(interacao.client, evento, interacao.guild)
            await atualizar_painel_presenca(interacao.client, evento.id)
            view_do_card = CardView(
                titulo="✅ Evento encerrado",
                linhas=[f"**{evento.titulo}** foi encerrado."],
                cor=COR_SUCESSO,
                timeout=None,
            )

        await interacao.response.edit_message(content=None, view=view_do_card)
        mensagem = await interacao.original_response()
        asyncio.create_task(excluir_mensagem(mensagem, delay=10))


class GateCog(commands.Cog):
    """Comandos e listeners do domínio GATE."""

    grupo_gate = app_commands.Group(
        name="gate",
        description="Consultas e atalhos dos eventos GATE",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Listener unificado (gate:* e presenca:*)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction):
        if interacao.type != discord.InteractionType.component:
            return

        custom_id = interacao.data.get("custom_id", "")

        if custom_id.startswith("gate:"):
            await self._tratar_botao_gate(interacao, custom_id)
            return

        if custom_id.startswith("presenca:"):
            await self._tratar_botao_presenca(interacao, custom_id)
            return

    async def _tratar_botao_gate(
        self,
        interacao: discord.Interaction,
        custom_id: str,
    ):
        if not tem_permissao_criar_evento(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Você não tem permissão para gerenciar eventos do GATE.",
                ],
            )
            return

        acao = custom_id.split(":", 1)[1]

        if acao == "treino":
            await interacao.response.send_modal(ModalTreino())
            return

        if acao == "facxfac":
            await interacao.response.send_modal(ModalFacXFac())
            return

        if acao == "dominas":
            await interacao.response.send_modal(ModalDominas())
            return

        if acao == "encerrar":
            eventos_abertos = await listar_eventos_abertos()

            if not eventos_abertos:
                await responder_aviso(
                    interacao,
                    titulo="Nenhum evento aberto",
                    linhas=["Não há eventos em aberto no momento."],
                )
                return

            linha_do_select = discord.ui.ActionRow()
            linha_do_select.add_item(SelectEncerrarEvento(eventos_abertos))

            await responder_card(
                interacao,
                titulo="Encerrar evento",
                linhas=["Selecione qual evento deseja encerrar:"],
                cor=COR_AVISO,
                extra_row=linha_do_select,
                delay=None,
            )

    async def _tratar_botao_presenca(
        self,
        interacao: discord.Interaction,
        custom_id: str,
    ):
        evento_id = int(custom_id.split(":", 1)[1])
        presencas = await listar_presencas(evento_id)

        membro_ja_confirmou = any(
            presenca.discord_id == interacao.user.id for presenca in presencas
        )

        if membro_ja_confirmou:
            resultado = await cancelar_presenca(evento_id, interacao.user.id)
            if resultado.ok:
                await responder_aviso(
                    interacao,
                    titulo="Presença cancelada",
                    linhas=[resultado.mensagem],
                )
                await atualizar_painel_presenca(interacao.client, evento_id)
            else:
                await responder_erro(
                    interacao,
                    titulo="Não foi possível cancelar",
                    linhas=[resultado.mensagem],
                )
            return

        # Ainda não confirmou → pede ID FiveM
        await interacao.response.send_modal(ModalConfirmarPresenca(evento_id))

    # ------------------------------------------------------------------
    # /gate listar
    # ------------------------------------------------------------------

    @grupo_gate.command(
        name="listar",
        description="Lista os eventos GATE abertos no momento",
    )
    async def listar(self, interacao: discord.Interaction):
        eventos = await listar_eventos_abertos()

        if not eventos:
            await responder_aviso(
                interacao,
                titulo="Eventos GATE",
                linhas=["Não há eventos abertos no momento."],
            )
            return

        linhas = []
        for evento in eventos:
            total = await contar_presencas(evento.id)
            if evento.limite_participantes > 0:
                texto_limite = f"{total}/{evento.limite_participantes}"
            else:
                texto_limite = f"{total} confirmados"

            linhas.append(
                f"**{evento.titulo}** · {evento.data_evento} {evento.horario} · "
                f"`{texto_limite}` · id `{evento.id}`"
            )

        await enviar_card(
            interacao,
            titulo=f"🛡️ Eventos abertos ({len(eventos)})",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /gate status
    # ------------------------------------------------------------------

    @grupo_gate.command(
        name="status",
        description="Mostra detalhes de um evento pelo ID",
    )
    @app_commands.describe(evento_id="ID numérico do evento")
    async def status(self, interacao: discord.Interaction, evento_id: int):
        evento = await buscar_evento_por_id(evento_id)

        if evento is None:
            await responder_erro(
                interacao,
                titulo="Evento não encontrado",
                linhas=[f"Não existe evento com id `{evento_id}`."],
            )
            return

        total = await contar_presencas(evento.id)
        if evento.limite_participantes > 0:
            texto_limite = f"{total}/{evento.limite_participantes}"
        else:
            texto_limite = f"{total} (sem limite)"

        linhas = [
            f"Título: **{evento.titulo}**",
            f"Tipo: `{evento.tipo}`",
            f"Status: **{evento.status}**",
            f"Data: {evento.data_evento} às {evento.horario}",
            f"Presenças: {texto_limite}",
            f"Responsável: <@{evento.responsavel_id}>",
            f"Criado por: <@{evento.criado_por}>",
        ]
        if evento.adversario:
            linhas.append(f"Adversário: **{evento.adversario}**")

        await enviar_card(
            interacao,
            titulo=f"📋 Evento #{evento.id}",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /gate meus
    # ------------------------------------------------------------------

    @grupo_gate.command(
        name="meus",
        description="Mostra em quais eventos abertos você confirmou presença",
    )
    async def meus(self, interacao: discord.Interaction):
        pares = await listar_presencas_do_membro(interacao.user.id)

        if not pares:
            await responder_info(
                interacao,
                titulo="Minhas presenças",
                linhas=["Você não confirmou presença em nenhum evento aberto."],
            )
            return

        linhas = [
            f"**{evento.titulo}** · {evento.data_evento} {evento.horario} · "
            f"ID FiveM `{presenca.id_fivem}`"
            for evento, presenca in pares
        ]

        await enviar_card(
            interacao,
            titulo=f"✅ Suas presenças ({len(pares)})",
            linhas=linhas,
            cor=COR_SUCESSO,
            delay=20,
        )

    # ------------------------------------------------------------------
    # /gate historico
    # ------------------------------------------------------------------

    @grupo_gate.command(
        name="historico",
        description="Mostra os últimos eventos GATE (abertos e encerrados)",
    )
    @app_commands.describe(quantidade="Quantos eventos mostrar (1 a 15)")
    async def historico(
        self,
        interacao: discord.Interaction,
        quantidade: app_commands.Range[int, 1, 15] = 8,
    ):
        eventos = await listar_ultimos_eventos(limite=quantidade)

        if not eventos:
            await responder_aviso(
                interacao,
                titulo="Histórico GATE",
                linhas=["Nenhum evento registrado ainda."],
            )
            return

        linhas = []
        for evento in eventos:
            emoji_status = "🟢" if evento.status == "aberto" else "⚫"
            linhas.append(
                f"{emoji_status} **{evento.titulo}** · "
                f"{evento.data_evento} · `{evento.status}` · id `{evento.id}`"
            )

        await enviar_card(
            interacao,
            titulo=f"📚 Últimos eventos ({len(eventos)})",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /gate ajuda
    # ------------------------------------------------------------------

    @grupo_gate.command(
        name="ajuda",
        description="Explica os comandos e o fluxo dos eventos GATE",
    )
    async def ajuda(self, interacao: discord.Interaction):
        await enviar_card(
            interacao,
            titulo="🛡️ Ajuda · GATE",
            linhas=[
                "**Painel** — use os botões no canal de eventos para criar ou encerrar.",
                "**Lista de presença** — confirme com o botão e informe seu ID FiveM.",
                "`/gate listar` — eventos abertos agora.",
                "`/gate status <id>` — detalhes de um evento.",
                "`/gate meus` — suas presenças em eventos abertos.",
                "`/gate historico` — últimos eventos criados.",
                "Só cargos autorizados criam/encerram eventos.",
                "Só membros da hierarquia GATE confirmam presença.",
            ],
            cor=COR_INFO,
            delay=40,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GateCog(bot))
