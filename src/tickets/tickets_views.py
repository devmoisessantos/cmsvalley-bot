"""
Views do ticket: cards de abertura + botões de staff no canal.
"""

from __future__ import annotations

import asyncio

import discord

from src.tickets.tickets_service import (
    adicionar_membro_ao_ticket,
    assumir_ticket,
    buscar_ticket_por_canal,
    coletar_mensagens_do_canal,
    criar_call_atendimento,
    finalizar_ticket,
    listar_categorias_ticket_na_guilda,
    membro_eh_staff_ticket,
    montar_html_transcript,
    mover_canal_ticket,
    nome_usuario_discord,
    remover_membro_do_ticket,
    transferir_atendimento,
    trocar_nome_do_canal,
)
from src.utils.mensagens import (
    COR_INFO,
    COR_SUCESSO,
    responder_card,
    responder_erro,
    responder_view,
)

# ---------------------------------------------------------------------------
# Cards enviados ao abrir o canal
# ---------------------------------------------------------------------------


class CardAberturaTicketView(discord.ui.LayoutView):
    """Card 1 — confirmação de abertura para o autor."""

    def __init__(self, autor: discord.Member, definicao: dict) -> None:
        super().__init__(timeout=None)

        emoji = definicao.get("emoji") or ""
        rotulo = definicao.get("rotulo") or "Ticket"

        texto = (
            f"### Todos os responsáveis pelo ticket já estão cientes da abertura\n"
            f"{autor.mention}, Evite chamar alguém via DM, basta aguardar "
            f"alguém já irá lhe atender...\n\n"
            f"**__Categoria Escolhida:__**\n"
            f"```fix\n"
            f"{emoji} {rotulo}\n"
            f"```\n"
            f"Lembrando que os botões são exclusivos para staffs!\n"
            f"\n"
            f"**`DESCREVA O MOTIVO DO CONTATO COM O MÁXIMO DE DETALHES "
            f"POSSÍVEIS QUE ALGUM RESPONSÁVEL JÁ IRÁ LHE ATENDER!`**\n"
        )

        container = discord.ui.Container(
            discord.ui.TextDisplay("# Ticket Criado com Sucesso! 📌"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(texto),
            accessory=discord.ui.Thumbnail(autor.display_avatar.url),
            accent_color=discord.Color.green(),
        )
        self.add_item(container)


class CardObservacaoDmView(discord.ui.LayoutView):
    """Card 2 — lembrete de DM aberta."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "## **`OBS: Procure manter sua DM aberta para receber "
                "uma cópia deste ticket e a opção de avaliar seu atendimento.`**"
            ),
            accent_color=discord.Color.orange(),
        )
        self.add_item(container)


class CardBotoesStaffView(discord.ui.LayoutView):
    """
    Card 3 — opções exclusivas dos responsáveis pelo atendimento.

    custom_ids fixos para sobreviver a reinícios do bot.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

        componentes: list = [
            discord.ui.TextDisplay(
                "-# Opções exclusivas para o uso dos responsáveis pelo atendimento!"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]

        linha_membros = discord.ui.ActionRow()
        linha_membros.add_item(
            discord.ui.Button(
                label="Adicionar Membro",
                emoji="➕",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:adicionar_membro",
            )
        )
        linha_membros.add_item(
            discord.ui.Button(
                label="Chamar Membro",
                emoji="👤",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:chamar_membro",
            )
        )
        linha_membros.add_item(
            discord.ui.Button(
                label="Remover Membro",
                emoji="➖",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:remover_membro",
            )
        )
        componentes.append(linha_membros)

        linha_canal = discord.ui.ActionRow()
        linha_canal.add_item(
            discord.ui.Button(
                label="Mover Canal",
                emoji="📂",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:mover_canal",
            )
        )
        linha_canal.add_item(
            discord.ui.Button(
                label="Trocar Nome do Canal",
                emoji="✏️",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:trocar_nome",
            )
        )
        componentes.append(linha_canal)

        linha_extras = discord.ui.ActionRow()
        linha_extras.add_item(
            discord.ui.Button(
                label="Adicionar Observação Interna",
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:obs_interna",
            )
        )
        linha_extras.add_item(
            discord.ui.Button(
                label="Criar Call de Atendimento",
                emoji="📞",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:criar_call",
            )
        )
        componentes.append(linha_extras)

        linha_atendimento = discord.ui.ActionRow()
        linha_atendimento.add_item(
            discord.ui.Button(
                label="Assumir Atendimento",
                emoji="🙋",
                style=discord.ButtonStyle.primary,
                custom_id="ticket:assumir",
            )
        )
        linha_atendimento.add_item(
            discord.ui.Button(
                label="Saudar Atendimento",
                emoji="👋",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:saudar",
            )
        )
        linha_atendimento.add_item(
            discord.ui.Button(
                label="Transferir Atendimento",
                emoji="🔄",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:transferir",
            )
        )
        componentes.append(linha_atendimento)

        linha_final = discord.ui.ActionRow()
        linha_final.add_item(
            discord.ui.Button(
                label="Finalizar Ticket",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id="ticket:finalizar",
            )
        )
        componentes.append(linha_final)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)


async def enviar_mensagens_abertura_ticket(
    canal: discord.TextChannel,
    autor: discord.Member,
    definicao: dict,
) -> None:
    """Envia os 3 cards de abertura no canal do ticket."""
    await canal.send(view=CardAberturaTicketView(autor=autor, definicao=definicao))
    await canal.send(view=CardObservacaoDmView())
    await canal.send(view=CardBotoesStaffView())


# ---------------------------------------------------------------------------
# Views auxiliares (seleção de membro / categoria)
# ---------------------------------------------------------------------------


class ViewSelecionarMembro(discord.ui.LayoutView):
    """Ephemeral: UserSelect para adicionar, remover, chamar ou transferir."""

    def __init__(
        self,
        acao: str,
        canal_id: int,
        ticket_id: int,
        autor_discord_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.acao = acao
        self.canal_id = canal_id
        self.ticket_id = ticket_id
        self.autor_discord_id = autor_discord_id

        titulos = {
            "adicionar": "Selecione o membro para **adicionar** ao ticket",
            "remover": "Selecione o membro para **remover** do ticket",
            "chamar": "Selecione o membro para **chamar** no canal",
            "transferir": "Selecione o staff que vai **assumir** o atendimento",
        }
        texto = titulos.get(acao, "Selecione um membro")

        seletor = discord.ui.UserSelect(
            placeholder="Escolha um membro…",
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_selecionar

        linha = discord.ui.ActionRow()
        linha.add_item(seletor)

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"### {texto}"),
            linha,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Nenhum membro foi selecionado."],
            )
            return

        membro_id = int(valores[0])
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Guilda não encontrada."],
            )
            return

        membro_alvo = guilda.get_member(membro_id)
        if membro_alvo is None:
            try:
                membro_alvo = await guilda.fetch_member(membro_id)
            except discord.HTTPException:
                membro_alvo = None

        if membro_alvo is None:
            await responder_erro(
                interacao,
                titulo="Membro não encontrado",
                linhas=["Não foi possível localizar este membro no servidor."],
            )
            return

        canal = guilda.get_channel(self.canal_id)
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=["O canal do ticket não foi encontrado."],
            )
            return

        staff = interacao.user
        if not isinstance(staff, discord.Member) or not membro_eh_staff_ticket(staff):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas a equipe de tickets pode usar esta ação."],
            )
            return

        if self.acao == "adicionar":
            await adicionar_membro_ao_ticket(canal, membro_alvo)
            await responder_card(
                interacao,
                titulo="Membro adicionado",
                linhas=[
                    f"{membro_alvo.mention} agora tem acesso a este ticket.",
                ],
                cor=COR_SUCESSO,
            )
            try:
                await canal.send(
                    content=(
                        f"➕ {membro_alvo.mention} foi adicionado ao ticket "
                        f"por {staff.mention}."
                    )
                )
            except discord.HTTPException:
                pass
            return

        if self.acao == "remover":
            erro = await remover_membro_do_ticket(
                canal,
                membro_alvo,
                self.autor_discord_id,
            )
            if erro:
                await responder_erro(
                    interacao,
                    titulo="Não foi possível remover",
                    linhas=[erro],
                )
                return
            await responder_card(
                interacao,
                titulo="Membro removido",
                linhas=[f"{membro_alvo.mention} perdeu o acesso a este ticket."],
                cor=COR_SUCESSO,
            )
            try:
                await canal.send(
                    content=(
                        f"➖ {membro_alvo.mention} foi removido do ticket "
                        f"por {staff.mention}."
                    )
                )
            except discord.HTTPException:
                pass
            return

        if self.acao == "chamar":
            await responder_card(
                interacao,
                titulo="Membro chamado",
                linhas=[f"{membro_alvo.mention} foi mencionado no canal."],
                cor=COR_SUCESSO,
            )
            try:
                await canal.send(
                    content=(
                        f"👤 {membro_alvo.mention}, você foi chamado neste ticket "
                        f"por {staff.mention}."
                    )
                )
            except discord.HTTPException:
                pass
            return

        if self.acao == "transferir":
            if not membro_eh_staff_ticket(membro_alvo):
                await responder_erro(
                    interacao,
                    titulo="Destino inválido",
                    linhas=[
                        "Só é possível transferir para quem tem cargo de "
                        "equipe de tickets ou diretoria.",
                    ],
                )
                return

            ticket = await buscar_ticket_por_canal(canal.id)
            if ticket is None:
                await responder_erro(
                    interacao,
                    titulo="Ticket não encontrado",
                    linhas=["Não foi possível localizar este ticket."],
                )
                return

            await transferir_atendimento(ticket, membro_alvo, canal)
            await responder_card(
                interacao,
                titulo="Atendimento transferido",
                linhas=[
                    f"Novo responsável: **{nome_usuario_discord(membro_alvo)}**",
                    "O canal foi renomeado.",
                ],
                cor=COR_SUCESSO,
            )
            try:
                await canal.send(
                    content=(
                        f"🔄 Atendimento transferido de {staff.mention} "
                        f"para {membro_alvo.mention}."
                    )
                )
            except discord.HTTPException:
                pass
            return


class ViewMoverCanal(discord.ui.LayoutView):
    """Ephemeral: select da categoria de destino."""

    def __init__(
        self,
        canal_id: int,
        opcoes: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=120)
        self.canal_id = canal_id

        seletor = discord.ui.Select(
            placeholder="Escolha a categoria de destino…",
            min_values=1,
            max_values=1,
            options=opcoes,
        )
        seletor.callback = self._ao_selecionar

        linha = discord.ui.ActionRow()
        linha.add_item(seletor)

        container = discord.ui.Container(
            discord.ui.TextDisplay("### 📂 Mover canal do ticket"),
            discord.ui.TextDisplay("Selecione a categoria de destino abaixo."),
            linha,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Nenhuma categoria foi selecionada."],
            )
            return

        categoria_id = int(valores[0])
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Guilda não encontrada."],
            )
            return

        categoria = guilda.get_channel(categoria_id)
        if not isinstance(categoria, discord.CategoryChannel):
            await responder_erro(
                interacao,
                titulo="Categoria inválida",
                linhas=["A categoria escolhida não existe mais."],
            )
            return

        canal = guilda.get_channel(self.canal_id)
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=["O canal do ticket não foi encontrado."],
            )
            return

        staff = interacao.user
        if not isinstance(staff, discord.Member):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Ação disponível apenas no servidor."],
            )
            return

        await mover_canal_ticket(canal, categoria, staff)
        await responder_card(
            interacao,
            titulo="Canal movido",
            linhas=[f"Nova categoria: **{categoria.name}**"],
            cor=COR_SUCESSO,
        )
        try:
            await canal.send(
                content=(
                    f"📂 Canal movido para **{categoria.name}** por {staff.mention}."
                )
            )
        except discord.HTTPException:
            pass


class ModalTrocarNome(discord.ui.Modal, title="Trocar nome do canal"):
    """Modal com o novo nome do canal."""

    novo_nome = discord.ui.TextInput(
        label="Novo nome do canal",
        style=discord.TextStyle.short,
        placeholder="ex: denuncia-jogador-fulano",
        required=True,
        max_length=90,
    )

    def __init__(self, canal_id: int) -> None:
        super().__init__()
        self.canal_id = canal_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        staff = interacao.user
        if not isinstance(staff, discord.Member) or not membro_eh_staff_ticket(staff):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas a equipe de tickets pode renomear o canal."],
            )
            return

        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Guilda não encontrada."],
            )
            return

        canal = guilda.get_channel(self.canal_id)
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=["O canal do ticket não foi encontrado."],
            )
            return

        nome_aplicado = await trocar_nome_do_canal(
            canal,
            str(self.novo_nome.value),
            staff,
        )
        await responder_card(
            interacao,
            titulo="Nome atualizado",
            linhas=[f"Novo nome: **#{nome_aplicado}**"],
            cor=COR_SUCESSO,
        )
        try:
            await canal.send(
                content=(
                    f"✏️ Nome do canal alterado para `#{nome_aplicado}` "
                    f"por {staff.mention}."
                )
            )
        except discord.HTTPException:
            pass


class ModalObservacaoInterna(discord.ui.Modal, title="Observação interna"):
    """Nota só para staff (publicada no canal com destaque)."""

    texto = discord.ui.TextInput(
        label="Observação",
        style=discord.TextStyle.paragraph,
        placeholder="Anotação interna sobre o atendimento…",
        required=True,
        max_length=1500,
    )

    def __init__(self, canal_id: int) -> None:
        super().__init__()
        self.canal_id = canal_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        staff = interacao.user
        if not isinstance(staff, discord.Member) or not membro_eh_staff_ticket(staff):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas a equipe de tickets pode registrar observação."],
            )
            return

        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Guilda não encontrada."],
            )
            return

        canal = guilda.get_channel(self.canal_id)
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=["O canal do ticket não foi encontrado."],
            )
            return

        conteudo = str(self.texto.value).strip()
        await responder_card(
            interacao,
            titulo="Observação registrada",
            linhas=["A nota interna foi publicada no canal."],
            cor=COR_SUCESSO,
        )
        try:
            await canal.send(
                content=(
                    f"📋 **Observação interna** (staff)\n"
                    f"Por {staff.mention}:\n"
                    f"> {conteudo}"
                )
            )
        except discord.HTTPException:
            pass


# ---------------------------------------------------------------------------
# Roteamento dos botões de staff
# ---------------------------------------------------------------------------

CUSTOM_IDS_STAFF = {
    "ticket:adicionar_membro",
    "ticket:chamar_membro",
    "ticket:remover_membro",
    "ticket:mover_canal",
    "ticket:trocar_nome",
    "ticket:obs_interna",
    "ticket:criar_call",
    "ticket:assumir",
    "ticket:saudar",
    "ticket:transferir",
    "ticket:finalizar",
}


async def processar_clique_botao_ticket(
    interacao: discord.Interaction,
) -> None:
    """Roteia cliques dos botões de staff dentro do canal do ticket."""
    custom_id = interacao.data.get("custom_id") if interacao.data else None
    if custom_id not in CUSTOM_IDS_STAFF:
        return

    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Esta ação só funciona dentro do servidor."],
        )
        return

    if not membro_eh_staff_ticket(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas a equipe de tickets pode usar estes botões."],
        )
        return

    canal = interacao.channel
    if not isinstance(canal, discord.TextChannel):
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Canal inválido."],
        )
        return

    ticket = await buscar_ticket_por_canal(canal.id)
    if ticket is None:
        await responder_erro(
            interacao,
            titulo="Ticket não encontrado",
            linhas=["Este canal não está registrado como ticket ativo."],
        )
        return

    if custom_id == "ticket:assumir":
        await _tratar_assumir(interacao, ticket, membro, canal)
        return

    if custom_id == "ticket:finalizar":
        await _tratar_finalizar(interacao, ticket, membro, canal)
        return

    if custom_id == "ticket:saudar":
        await _tratar_saudar(interacao, ticket, membro, canal)
        return

    if custom_id == "ticket:adicionar_membro":
        await responder_view(
            interacao,
            view=ViewSelecionarMembro(
                acao="adicionar",
                canal_id=canal.id,
                ticket_id=ticket.id,
                autor_discord_id=ticket.autor_discord_id,
            ),
            ephemeral=True,
        )
        return

    if custom_id == "ticket:remover_membro":
        await responder_view(
            interacao,
            view=ViewSelecionarMembro(
                acao="remover",
                canal_id=canal.id,
                ticket_id=ticket.id,
                autor_discord_id=ticket.autor_discord_id,
            ),
            ephemeral=True,
        )
        return

    if custom_id == "ticket:chamar_membro":
        await responder_view(
            interacao,
            view=ViewSelecionarMembro(
                acao="chamar",
                canal_id=canal.id,
                ticket_id=ticket.id,
                autor_discord_id=ticket.autor_discord_id,
            ),
            ephemeral=True,
        )
        return

    if custom_id == "ticket:transferir":
        await responder_view(
            interacao,
            view=ViewSelecionarMembro(
                acao="transferir",
                canal_id=canal.id,
                ticket_id=ticket.id,
                autor_discord_id=ticket.autor_discord_id,
            ),
            ephemeral=True,
        )
        return

    if custom_id == "ticket:trocar_nome":
        await interacao.response.send_modal(ModalTrocarNome(canal_id=canal.id))
        return

    if custom_id == "ticket:obs_interna":
        await interacao.response.send_modal(ModalObservacaoInterna(canal_id=canal.id))
        return

    if custom_id == "ticket:mover_canal":
        await _tratar_mover_canal(interacao, canal)
        return

    if custom_id == "ticket:criar_call":
        await _tratar_criar_call(interacao, ticket, membro, canal)
        return


async def _tratar_mover_canal(
    interacao: discord.Interaction,
    canal: discord.TextChannel,
) -> None:
    guilda = interacao.guild
    if guilda is None:
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Guilda não encontrada."],
        )
        return

    pares = listar_categorias_ticket_na_guilda(guilda)
    if not pares:
        await responder_erro(
            interacao,
            titulo="Sem categorias",
            linhas=["Nenhuma categoria de ticket foi encontrada na guilda."],
        )
        return

    opcoes: list[discord.SelectOption] = []
    for rotulo, categoria in pares:
        opcoes.append(
            discord.SelectOption(
                label=rotulo[:100],
                value=str(categoria.id),
                description=f"#{categoria.name}"[:100],
            )
        )

    await responder_view(
        interacao,
        view=ViewMoverCanal(canal_id=canal.id, opcoes=opcoes),
        ephemeral=True,
    )


async def _tratar_criar_call(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    guilda = interacao.guild
    if guilda is None:
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Guilda não encontrada."],
        )
        return

    try:
        canal_voz = await criar_call_atendimento(guilda, canal, ticket, membro)
    except discord.HTTPException as erro:
        await responder_erro(
            interacao,
            titulo="Falha ao criar call",
            linhas=[f"O Discord recusou a criação: {erro}"],
        )
        return

    await responder_card(
        interacao,
        titulo="Call criada",
        linhas=[
            f"Canal de voz: {canal_voz.mention}",
            "Autor e equipe de tickets já têm acesso.",
        ],
        cor=COR_SUCESSO,
    )
    try:
        await canal.send(
            content=(
                f"📞 Call de atendimento criada: {canal_voz.mention}\n"
                f"Por {membro.mention}."
            )
        )
    except discord.HTTPException:
        pass


async def _tratar_assumir(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    if ticket.status == "finalizado":
        await responder_erro(
            interacao,
            titulo="Ticket já finalizado",
            linhas=["Este ticket já foi encerrado."],
        )
        return

    if ticket.status == "assumido" and ticket.staff_assumiu_id == membro.id:
        await responder_card(
            interacao,
            titulo="Já é seu",
            linhas=["Você já assumiu este atendimento."],
            cor=COR_INFO,
        )
        return

    ticket_atualizado = await assumir_ticket(ticket, membro, canal)

    await responder_card(
        interacao,
        titulo="Atendimento assumido",
        linhas=[
            f"Staff: **{nome_usuario_discord(membro)}**",
            f"Categoria: {ticket_atualizado.categoria_rotulo}",
            "O canal foi renomeado.",
        ],
        cor=COR_SUCESSO,
    )

    try:
        await canal.send(
            content=(f"🎫 **Atendimento Assumido**\nAssumido por {membro.mention}")
        )
    except discord.HTTPException:
        pass


async def _tratar_saudar(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    await responder_card(
        interacao,
        titulo="Saudação enviada",
        linhas=["Mensagem de boas-vindas publicada no canal."],
        cor=COR_SUCESSO,
    )
    try:
        await canal.send(
            content=(
                f"👋 Olá <@{ticket.autor_discord_id}>! "
                f"Sou {membro.mention} e vou cuidar do seu atendimento. "
                f"Pode descrever o motivo do contato com o máximo de detalhes."
            )
        )
    except discord.HTTPException:
        pass


async def _tratar_finalizar(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    if ticket.status == "finalizado":
        await responder_erro(
            interacao,
            titulo="Já finalizado",
            linhas=["Este ticket já foi encerrado."],
        )
        return

    await interacao.response.send_modal(ModalFinalizarTicket(ticket_id=ticket.id))


class ModalFinalizarTicket(discord.ui.Modal, title="Finalizar ticket"):
    """Coleta considerações finais antes de fechar."""

    consideracoes = discord.ui.TextInput(
        label="Considerações finais",
        style=discord.TextStyle.paragraph,
        placeholder="Resumo do atendimento (opcional)",
        required=False,
        max_length=1000,
    )

    def __init__(self, ticket_id: int) -> None:
        super().__init__()
        self.ticket_id = ticket_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Ação disponível apenas no servidor."],
            )
            return

        if not membro_eh_staff_ticket(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas a equipe de tickets pode finalizar."],
            )
            return

        canal = interacao.channel
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Canal inválido."],
            )
            return

        ticket = await buscar_ticket_por_canal(canal.id)
        if ticket is None or ticket.id != self.ticket_id:
            await responder_erro(
                interacao,
                titulo="Ticket não encontrado",
                linhas=["Não foi possível localizar este ticket."],
            )
            return

        if ticket.status == "finalizado":
            await responder_erro(
                interacao,
                titulo="Já finalizado",
                linhas=["Este ticket já foi encerrado."],
            )
            return

        texto_consideracoes = str(self.consideracoes.value or "").strip() or None
        ticket_final = await finalizar_ticket(
            ticket,
            membro,
            consideracoes=texto_consideracoes,
        )

        mensagens = await coletar_mensagens_do_canal(canal)
        _html = montar_html_transcript(ticket_final, mensagens, interacao.guild)

        await responder_card(
            interacao,
            titulo="Ticket finalizado",
            linhas=[
                f"Finalizado por: **{nome_usuario_discord(membro)}**",
                f"Senha do transcript: `{ticket_final.senha_transcript}`",
                "O canal será apagado em breve.",
                "Transcript gerado (envio para o site na próxima fase).",
            ],
            cor=COR_SUCESSO,
            delay=None,
        )

        try:
            await canal.send(
                content=(
                    f"🎫 **Ticket Finalizado**\n"
                    f"Finalizado por {membro.mention}\n"
                    f"Senha do transcript: `{ticket_final.senha_transcript}`\n"
                    f"Considerações: {texto_consideracoes or '—'}"
                )
            )
        except discord.HTTPException:
            pass

        await asyncio.sleep(8)
        try:
            await canal.delete(
                reason=f"Ticket #{ticket_final.id} finalizado por {membro}"
            )
        except discord.HTTPException:
            pass
