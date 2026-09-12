"""
Cog de tickets: views persistentes, botões e comandos de barra.

O grupo `/ticket` expõe as ações de staff no canal atual e a
consulta de transcript por ID do autor.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.tickets.tickets_panel import (
    PainelTicketDenunciasLayout,
    PainelTicketSuporteLayout,
    processar_clique_abrir_ticket,
)
from src.tickets.tickets_service import (
    buscar_ticket_por_canal,
    listar_tickets_abertos,
    listar_tickets_do_autor,
    membro_pode_gerenciar_ticket,
    nome_usuario_discord,
    sincronizar_permissoes_do_canal_ticket,
)
from src.tickets.tickets_views import (
    CUSTOM_IDS_STAFF,
    CardBotoesStaffView,
    ModalFinalizarTicket,
    ModalTrocarNome,
    _tratar_assumir,
    _tratar_chamar_autor,
    _tratar_saudar,
    processar_clique_botao_ticket,
)
from src.utils.formatacao import para_horario_brasilia
from src.utils.mensagens import (
    COR_INFO,
    COR_SUCESSO,
    CardView,
    responder_card,
    responder_erro,
    responder_view,
)
from src.utils.permissions import membro_e_administrador

registrador = logging.getLogger(__name__)


class ViewEscolherTranscript(discord.ui.LayoutView):
    """
    Lista tickets do autor para o staff escolher qual transcript ver.

    Cada opção leva à senha e ao link público, quando existirem.
    """

    def __init__(
        self,
        tickets: list,
        autor_rotulo: str,
        solicitante_id: int,
    ) -> None:
        super().__init__(timeout=180)
        self.tickets_por_id = {str(ticket.id): ticket for ticket in tickets}
        self.solicitante_id = solicitante_id

        opcoes: list[discord.SelectOption] = []
        for ticket in tickets[:25]:
            data_abertura = para_horario_brasilia(ticket.aberto_em)
            if data_abertura is not None:
                texto_data = data_abertura.strftime("%d/%m/%Y %H:%M")
            else:
                texto_data = "—"

            status = ticket.status or "—"
            rotulo = f"#{ticket.id} · {status}"[:100]
            descricao = (
                f"{ticket.categoria_rotulo or ticket.categoria_chave} · {texto_data}"
            )[:100]
            opcoes.append(
                discord.SelectOption(
                    label=rotulo,
                    value=str(ticket.id),
                    description=descricao,
                )
            )

        texto = (
            f"# 📜 Transcripts de {autor_rotulo}\n"
            f"> Selecione o atendimento para ver senha e link do transcript."
        )
        componentes: list = [discord.ui.TextDisplay(texto)]

        if not opcoes:
            componentes.append(
                discord.ui.TextDisplay("Nenhum ticket encontrado para este usuário.")
            )
        else:
            seletor = discord.ui.Select(
                placeholder="Escolha o ticket…",
                min_values=1,
                max_values=1,
                options=opcoes,
            )
            seletor.callback = self._ao_selecionar
            linha = discord.ui.ActionRow()
            linha.add_item(seletor)
            componentes.append(linha)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        """Mostra senha e link do transcript do ticket escolhido."""
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Seleção de outra pessoa",
                linhas=["Só quem abriu este menu pode escolher o ticket."],
            )
            return

        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Não recebi o ticket escolhido."],
            )
            return

        ticket = self.tickets_por_id.get(str(valores[0]))
        if ticket is None:
            await responder_erro(
                interacao,
                titulo="Ticket não encontrado",
                linhas=["Esse ticket não está mais na lista."],
            )
            return

        data_abertura = para_horario_brasilia(ticket.aberto_em)
        if data_abertura is not None:
            texto_abertura = data_abertura.strftime("%d/%m/%Y às %H:%M")
        else:
            texto_abertura = "—"

        senha = ticket.senha_transcript or "—"
        url = (ticket.url_transcript or "").strip()
        staff_nome = ticket.staff_assumiu_nome or "—"
        status = ticket.status or "—"

        linhas = [
            f"**ID:** `{ticket.id}`",
            f"**Status:** `{status}`",
            f"**Categoria:** `{ticket.categoria_rotulo or ticket.categoria_chave}`",
            f"**Aberto em:** `{texto_abertura}`",
            f"**Staff:** `{staff_nome}`",
            f"**Senha:** ||`{senha}`||",
        ]
        if url:
            linhas.append(f"**Link:** {url}")
        else:
            linhas.append(
                "**Link:** _ainda não publicado (ticket pode não ter sido finalizado)._"
            )

        extra_row = None
        if url:
            extra_row = discord.ui.ActionRow()
            extra_row.add_item(
                discord.ui.Button(
                    label="Abrir transcript",
                    style=discord.ButtonStyle.link,
                    url=url,
                    emoji="📜",
                )
            )

        view = CardView(
            titulo=f"Transcript · ticket #{ticket.id}",
            linhas=linhas,
            cor=COR_INFO,
            timeout=180,
            extra_row=extra_row,
            com_marcador=True,
        )
        await responder_view(interacao, view, ephemeral=True)


class TicketsCog(commands.Cog):
    """Listeners, views persistentes e comandos de barra do domínio tickets."""

    grupo_ticket = app_commands.Group(
        name="ticket",
        description="Ações de staff no ticket atual e consulta de transcripts",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Registra interfaces persistentes para que botões sobrevivam a reinícios."""
        self.bot.add_view(PainelTicketSuporteLayout())
        self.bot.add_view(PainelTicketDenunciasLayout())
        self.bot.add_view(CardBotoesStaffView())

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction) -> None:
        """
        Captura:
        - botões do painel (ticket:abrir:...)
        - botões de staff no canal (ticket:assumir, ticket:finalizar, etc.)
        """
        if interacao.type != discord.InteractionType.component:
            return

        custom_id = ""
        if interacao.data:
            custom_id = interacao.data.get("custom_id") or ""

        if custom_id.startswith("ticket:abrir:"):
            await processar_clique_abrir_ticket(interacao)
            return

        if custom_id in CUSTOM_IDS_STAFF:
            await processar_clique_botao_ticket(interacao)
            return

    # ------------------------------------------------------------------
    # Helpers dos comandos /ticket
    # ------------------------------------------------------------------

    async def _resolver_ticket_do_canal(
        self,
        interacao: discord.Interaction,
    ) -> tuple[discord.Member | None, discord.TextChannel | None, object | None]:
        """
        Valida membro, canal de texto e ticket ativo no canal atual.

        Retorna (membro, canal, ticket) ou (None, None, None) quando já
        respondeu o erro ao usuário.
        """
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Comando indisponível aqui",
                linhas=["Esta ação só funciona dentro do servidor."],
            )
            return None, None, None

        canal = interacao.channel
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=[
                    "Use este comando dentro do canal de texto do ticket.",
                ],
            )
            return None, None, None

        ticket = await buscar_ticket_por_canal(canal.id)
        if ticket is None:
            await responder_erro(
                interacao,
                titulo="Ticket não encontrado",
                linhas=["Este canal não está registrado como ticket ativo."],
            )
            return None, None, None

        if not membro_pode_gerenciar_ticket(membro, ticket):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Este comando é liberado para administrador, "
                    "Responsavel HP, Responsável Geral ou quem "
                    "assumiu este ticket.",
                ],
            )
            return None, None, None

        return membro, canal, ticket

    # ------------------------------------------------------------------
    # Comandos /ticket atual-*
    # ------------------------------------------------------------------

    @grupo_ticket.command(
        name="atual-finalizar",
        description="Finaliza o ticket do canal atual",
    )
    async def ticket_atual_finalizar(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Abre o modal de considerações e encerra o atendimento atual."""
        membro, canal, ticket = await self._resolver_ticket_do_canal(interacao)
        if ticket is None or membro is None or canal is None:
            return

        if ticket.status == "finalizado":
            await responder_erro(
                interacao,
                titulo="Já finalizado",
                linhas=["Este ticket já foi encerrado."],
            )
            return

        await interacao.response.send_modal(ModalFinalizarTicket(ticket_id=ticket.id))

    @grupo_ticket.command(
        name="atual-chamar-membro",
        description="Chama por DM o autor do ticket do canal atual",
    )
    async def ticket_atual_chamar_membro(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Notifica apenas o autor do ticket (sem escolher outro membro)."""
        membro, canal, ticket = await self._resolver_ticket_do_canal(interacao)
        if ticket is None or membro is None or canal is None:
            return

        await _tratar_chamar_autor(interacao, ticket, membro, canal)

    @grupo_ticket.command(
        name="atual-assumir",
        description="Assume o ticket do canal atual",
    )
    async def ticket_atual_assumir(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Marca o staff como responsável pelo atendimento deste canal."""
        membro, canal, ticket = await self._resolver_ticket_do_canal(interacao)
        if ticket is None or membro is None or canal is None:
            return

        await _tratar_assumir(interacao, ticket, membro, canal)

    @grupo_ticket.command(
        name="atual-saudar",
        description="Envia a saudação inicial no ticket do canal atual",
    )
    async def ticket_atual_saudar(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Publica a saudação padrão e marca o ticket como saudado."""
        membro, canal, ticket = await self._resolver_ticket_do_canal(interacao)
        if ticket is None or membro is None or canal is None:
            return

        await _tratar_saudar(interacao, ticket, membro, canal)

    @grupo_ticket.command(
        name="atual-trocar-nome",
        description="Troca o nome do canal do ticket atual",
    )
    async def ticket_atual_trocar_nome(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Abre o modal para renomear o canal do ticket atual."""
        membro, canal, ticket = await self._resolver_ticket_do_canal(interacao)
        if ticket is None or membro is None or canal is None:
            return

        await interacao.response.send_modal(ModalTrocarNome(canal_id=canal.id))

    @grupo_ticket.command(
        name="ver-transcript",
        description="Busca transcripts pelo Discord ID do autor",
    )
    @app_commands.describe(
        id_do_usuario="Discord ID do autor do ticket",
    )
    async def ticket_ver_transcript(
        self,
        interacao: discord.Interaction,
        id_do_usuario: str,
    ) -> None:
        """
        Lista tickets do autor e deixa o staff escolher qual transcript ver.

        Não exige estar no canal do ticket. Continua restrito a
        administrador, Responsavel HP, Responsável Geral ou quem
        assumiu algum ticket (checagem sem ticket específico).
        """
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Comando indisponível aqui",
                linhas=["Esta ação só funciona dentro do servidor."],
            )
            return

        if not membro_pode_gerenciar_ticket(membro, ticket=None):
            # Quem só assumiu tickets não passa sem ticket; admin e
            # responsáveis de HP/Geral passam. Amplia um pouco: staff
            # de equipe de ticket também consulta histórico.
            from src.tickets.tickets_service import membro_eh_staff_ticket

            if not membro_eh_staff_ticket(membro):
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=[
                        "Este comando é liberado para administrador, "
                        "Responsavel HP, Responsável Geral ou equipe "
                        "de tickets.",
                    ],
                )
                return

        texto_id = (id_do_usuario or "").strip()
        if not texto_id.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=[
                    "Informe apenas números do Discord ID do autor.",
                    "Exemplo: `123456789012345678`",
                ],
            )
            return

        autor_id = int(texto_id)
        tickets = await listar_tickets_do_autor(autor_id, limite=25)
        if not tickets:
            await responder_erro(
                interacao,
                titulo="Nenhum ticket",
                linhas=[
                    f"Não encontrei tickets para o ID `{autor_id}`.",
                ],
            )
            return

        autor_rotulo = str(autor_id)
        guilda = interacao.guild
        if guilda is not None:
            membro_autor = guilda.get_member(autor_id)
            if membro_autor is not None:
                autor_rotulo = f"{nome_usuario_discord(membro_autor)} (`{autor_id}`)"

        view = ViewEscolherTranscript(
            tickets=tickets,
            autor_rotulo=autor_rotulo,
            solicitante_id=membro.id,
        )
        await responder_view(interacao, view, ephemeral=True)

    @grupo_ticket.command(
        name="sincronizar-permissoes",
        description=(
            "Aplica nos canais os cargos de staff (ticket atual ou todos abertos)"
        ),
    )
    @app_commands.describe(
        todos_abertos=(
            "Se verdadeiro, corrige todos os tickets abertos/assumidos do servidor"
        ),
    )
    async def ticket_sincronizar_permissoes(
        self,
        interacao: discord.Interaction,
        todos_abertos: bool = False,
    ) -> None:
        """
        Reaplica overwrites de staff nos canais de ticket.

        Use no canal do ticket para corrigir só ele, ou marque
        todos_abertos para varrer os tickets ainda ativos.
        """
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Comando indisponível aqui",
                linhas=["Esta ação só funciona dentro do servidor."],
            )
            return

        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Servidor não encontrado",
                linhas=["Guilda não encontrada."],
            )
            return

        if not membro_e_administrador(membro):
            if not membro_pode_gerenciar_ticket(membro, ticket=None):
                from src.tickets.tickets_service import membro_eh_staff_ticket

                if not membro_eh_staff_ticket(membro):
                    await responder_erro(
                        interacao,
                        titulo="Sem permissão",
                        linhas=[
                            "Apenas administrador ou staff de tickets "
                            "pode sincronizar permissões.",
                        ],
                    )
                    return

        await interacao.response.defer(ephemeral=True)

        if not todos_abertos:
            canal = interacao.channel
            if not isinstance(canal, discord.TextChannel):
                await responder_erro(
                    interacao,
                    titulo="Canal inválido",
                    linhas=[
                        "Use dentro do canal do ticket, ou marque `todos_abertos`.",
                    ],
                )
                return

            ticket = await buscar_ticket_por_canal(canal.id)
            if ticket is None:
                await responder_erro(
                    interacao,
                    titulo="Ticket não encontrado",
                    linhas=["Este canal não é um ticket ativo."],
                )
                return

            resultado = await sincronizar_permissoes_do_canal_ticket(
                canal,
                ticket,
            )
            nomes_ok = resultado.get("nomes_ok") or []
            nomes_falha = resultado.get("nomes_falha") or []
            linhas = [
                f"**Ticket:** `#{ticket.id}` · `{ticket.categoria_chave}`",
                f"**Cargos aplicados:** `{resultado.get('cargos_ok', 0)}`",
                f"**Falhas:** `{resultado.get('cargos_falha', 0)}`",
            ]
            if nomes_ok:
                trecho = ", ".join(str(nome) for nome in nomes_ok[:12])
                linhas.append(f"**OK:** {trecho}")
            if nomes_falha:
                trecho_falha = ", ".join(str(nome) for nome in nomes_falha[:8])
                linhas.append(f"**Falha:** {trecho_falha}")

            await responder_card(
                interacao,
                titulo="Permissões sincronizadas",
                linhas=linhas,
                cor=COR_SUCESSO,
                delay=30,
            )
            return

        tickets = await listar_tickets_abertos(limite=100)
        if not tickets:
            await responder_erro(
                interacao,
                titulo="Nenhum ticket aberto",
                linhas=["Não há tickets com status aberto ou assumido."],
            )
            return

        total_ok = 0
        total_falha = 0
        canais_ok = 0
        canais_sem_canal = 0

        for ticket in tickets:
            canal = guilda.get_channel(int(ticket.canal_id))
            if not isinstance(canal, discord.TextChannel):
                canais_sem_canal += 1
                continue
            resultado = await sincronizar_permissoes_do_canal_ticket(
                canal,
                ticket,
            )
            total_ok += int(resultado.get("cargos_ok") or 0)
            total_falha += int(resultado.get("cargos_falha") or 0)
            canais_ok += 1

        await responder_card(
            interacao,
            titulo="Sincronização em lote concluída",
            linhas=[
                f"**Tickets processados:** `{canais_ok}`",
                f"**Canais não encontrados:** `{canais_sem_canal}`",
                f"**Cargos aplicados (soma):** `{total_ok}`",
                f"**Falhas (soma):** `{total_falha}`",
                "Confira um canal de ticket: equipe ticket e "
                "diretoria devem aparecer nas permissões.",
            ],
            cor=COR_SUCESSO,
            delay=45,
        )


async def setup(bot: commands.Bot) -> None:
    """Adiciona ao bot os listeners e as interfaces do domínio de tickets."""
    await bot.add_cog(TicketsCog(bot))
