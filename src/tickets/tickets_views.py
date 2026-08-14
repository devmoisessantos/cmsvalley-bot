"""
Views do ticket: cards de abertura + botões de staff no canal.
"""

from __future__ import annotations

import asyncio

import discord

from src.tickets.tickets_logger import enviar_log_ticket_finalizado
from src.tickets.tickets_service import (
    adicionar_membro_ao_ticket,
    apagar_call_do_ticket,
    assumir_ticket,
    buscar_ticket_por_canal,
    buscar_ticket_por_id,
    coletar_mensagens_do_canal,
    criar_call_atendimento,
    enviar_card_no_canal_ticket,
    finalizar_ticket,
    listar_categorias_ticket_na_guilda,
    membro_eh_staff_ticket,
    montar_html_transcript,
    mover_canal_ticket,
    nome_usuario_discord,
    remover_membro_do_ticket,
    salvar_call_canal_id,
    salvar_mensagem_botoes_id,
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
from src.utils.notificacao import (
    COR_INFO as COR_DM_INFO,
    enviar_dm_card,
)


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
            discord.ui.Section(
                texto,
                accessory=discord.ui.Thumbnail(autor.display_avatar.url),
            ),
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

    Estado dinâmico:
    - se já assumido → botão Assumir vira «Assumido por: …» e fica desativado
    - se há call → botão vira «Encerrar call de atendimento»
    """

    def __init__(
        self,
        staff_assumiu_id: int | None = None,
        staff_assumiu_label: str | None = None,
        call_ativa: bool = False,
    ) -> None:
        super().__init__(timeout=None)

        componentes: list = [
            discord.ui.TextDisplay(
                "-# Opções exclusivas para o uso dos responsáveis pelo atendimento!"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
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
        if call_ativa:
            linha_extras.add_item(
                discord.ui.Button(
                    label="Encerrar call de atendimento",
                    emoji="📞",
                    style=discord.ButtonStyle.danger,
                    custom_id="ticket:encerrar_call",
                )
            )
        else:
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
        if staff_assumiu_id is not None:
            rotulo_assumido = staff_assumiu_label or "Staff"
            if len(rotulo_assumido) > 70:
                rotulo_assumido = rotulo_assumido[:67] + "…"
            linha_atendimento.add_item(
                discord.ui.Button(
                    label=f"Assumido por: {rotulo_assumido}",
                    emoji="🙋",
                    style=discord.ButtonStyle.secondary,
                    custom_id="ticket:assumir",
                    disabled=True,
                )
            )
        else:
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
    ticket_id: int,
) -> None:
    """Envia os 3 cards de abertura e salva o ID da mensagem de botões."""
    await canal.send(view=CardAberturaTicketView(autor=autor, definicao=definicao))
    await canal.send(view=CardObservacaoDmView())
    mensagem_botoes = await canal.send(view=CardBotoesStaffView())
    await salvar_mensagem_botoes_id(ticket_id, mensagem_botoes.id)


async def atualizar_card_botoes_staff(
    canal: discord.TextChannel,
    ticket,
) -> None:
    """Reenvia o estado dos botões (Assumir desativado / Encerrar call)."""
    if not ticket.mensagem_botoes_id:
        return

    try:
        mensagem = await canal.fetch_message(int(ticket.mensagem_botoes_id))
    except discord.HTTPException:
        return

    label_assumido = None
    if ticket.staff_assumiu_id:
        label_assumido = ticket.staff_assumiu_nome or str(ticket.staff_assumiu_id)

    view = CardBotoesStaffView(
        staff_assumiu_id=ticket.staff_assumiu_id,
        staff_assumiu_label=label_assumido,
        call_ativa=bool(ticket.call_canal_id),
    )
    try:
        await mensagem.edit(view=view)
    except discord.HTTPException:
        pass


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
            await interacao.response.defer(ephemeral=True)
            await enviar_card_no_canal_ticket(
                canal,
                titulo="➕ Usuário Adicionado ao Ticket",
                linhas=[
                    f"{membro_alvo.mention} foi adicionado ao ticket por {staff.mention}",
                ],
                cor=COR_SUCESSO,
            )
            await interacao.followup.send(content="Membro adicionado.", ephemeral=True)
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
            await interacao.response.defer(ephemeral=True)
            await enviar_card_no_canal_ticket(
                canal,
                titulo="➖ Usuário Removido do Ticket",
                linhas=[
                    f"{membro_alvo.mention} foi removido do ticket por {staff.mention}.",
                ],
                cor=COR_INFO,
            )
            await interacao.followup.send(content="Membro removido.", ephemeral=True)
            return

        if self.acao == "chamar":
            await interacao.response.defer(ephemeral=True)
            await enviar_card_no_canal_ticket(
                canal,
                titulo="👤 Usuário Chamado no Ticket",
                linhas=[
                    f"{membro_alvo.mention} foi chamado neste ticket por {staff.mention}.",
                ],
                cor=COR_INFO,
            )
            await interacao.followup.send(
                content="Membro chamado no canal.", ephemeral=True
            )
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

            await interacao.response.defer(ephemeral=True)
            ticket_atualizado = await transferir_atendimento(ticket, membro_alvo, canal)
            await atualizar_card_botoes_staff(canal, ticket_atualizado)
            await enviar_card_no_canal_ticket(
                canal,
                titulo="🔄 Atendimento Transferido",
                linhas=[
                    f"Atendimento transferido de {staff.mention} "
                    f"para {membro_alvo.mention}.",
                ],
                cor=COR_SUCESSO,
            )
            await interacao.followup.send(
                content="Atendimento transferido.", ephemeral=True
            )
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

        await interacao.response.defer(ephemeral=True)
        await mover_canal_ticket(canal, categoria, staff)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="📂 Canal Movido",
            linhas=[
                f"Canal movido para {categoria.name} por {staff.mention}.",
            ],
            cor=COR_INFO,
        )
        await interacao.followup.send(content="Canal movido.", ephemeral=True)


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
        await interacao.response.defer(ephemeral=True)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="✏️ Nome do Canal Atualizado",
            linhas=[
                f"Atualizado por {staff.mention} para `{nome_aplicado}`",
            ],
            cor=COR_INFO,
        )
        await interacao.followup.send(content="Nome atualizado.", ephemeral=True)


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
        await interacao.response.defer(ephemeral=True)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="📋 Observação Interna (Responsavel)",
            linhas=[
                f"> **{staff.mention}:** {conteudo}",
            ],
            cor=COR_INFO,
        )
        await interacao.followup.send(content="Observação registrada.", ephemeral=True)


CUSTOM_IDS_STAFF = {
    "ticket:adicionar_membro",
    "ticket:chamar_membro",
    "ticket:remover_membro",
    "ticket:mover_canal",
    "ticket:trocar_nome",
    "ticket:obs_interna",
    "ticket:criar_call",
    "ticket:encerrar_call",
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

    if custom_id == "ticket:encerrar_call":
        await _tratar_encerrar_call(interacao, ticket, membro, canal)
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

    if ticket.call_canal_id:
        await responder_erro(
            interacao,
            titulo="Call já existe",
            linhas=["Já existe uma call de atendimento neste ticket."],
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

    await salvar_call_canal_id(ticket.id, canal_voz.id)
    ticket_atualizado = await buscar_ticket_por_id(ticket.id)
    if ticket_atualizado is not None:
        await atualizar_card_botoes_staff(canal, ticket_atualizado)

    await interacao.response.defer(ephemeral=True)
    await enviar_card_no_canal_ticket(
        canal,
        titulo="📞 Call de Atendimento",
        linhas=[
            f"Call criada: {canal_voz.mention}",
            f"Por {membro.mention}.",
        ],
        cor=COR_SUCESSO,
    )
    await interacao.followup.send(content="Call criada.", ephemeral=True)


async def _tratar_encerrar_call(
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

    await interacao.response.defer(ephemeral=True)
    await apagar_call_do_ticket(guilda, ticket)
    ticket_atualizado = await buscar_ticket_por_id(ticket.id)
    if ticket_atualizado is not None:
        await atualizar_card_botoes_staff(canal, ticket_atualizado)

    await enviar_card_no_canal_ticket(
        canal,
        titulo="📞 Call Encerrada",
        linhas=[f"Call de atendimento encerrada por {membro.mention}."],
        cor=COR_INFO,
    )
    await interacao.followup.send(content="Call encerrada.", ephemeral=True)


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

    await interacao.response.defer(ephemeral=True)
    ticket_atualizado = await assumir_ticket(ticket, membro, canal)
    await atualizar_card_botoes_staff(canal, ticket_atualizado)

    username = nome_usuario_discord(membro)
    await enviar_card_no_canal_ticket(
        canal,
        titulo="🎫 Atendimento Assumido",
        linhas=[
            f"Assumido por {membro.mention} canal alterado para o novo nome: "
            f"`🙋・{username}`",
        ],
        cor=COR_SUCESSO,
    )
    await enviar_card_no_canal_ticket(
        canal,
        titulo="🎫 Atendimento Assumido",
        linhas=[
            f"Este atendimento foi assumido por {membro.mention}.",
        ],
        cor=COR_SUCESSO,
    )
    await interacao.followup.send(content="Atendimento assumido.", ephemeral=True)


async def _tratar_saudar(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    await interacao.response.defer(ephemeral=True)
    await enviar_card_no_canal_ticket(
        canal,
        titulo="👋 Saudação Inicial",
        linhas=[
            f"Olá <@{ticket.autor_discord_id}>! Sou {membro.mention} e vou "
            f"cuidar do seu atendimento. Pode descrever o motivo do contato "
            f"com o máximo de detalhes.",
        ],
        cor=COR_INFO,
    )
    await interacao.followup.send(content="Saudação enviada.", ephemeral=True)


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
        if not texto_consideracoes:
            texto_consideracoes = "Atendimento Finalizado"

        await interacao.response.defer(ephemeral=True)

        nome_canal_atual = canal.name
        username_staff = nome_usuario_discord(membro)

        await enviar_card_no_canal_ticket(
            canal,
            titulo="🎫 Ticket Finalizado",
            linhas=[
                f"Este ticket acaba de ser finalizado pelo responsavel "
                f"{membro.mention} / `{username_staff}`!",
                "Considerações Finais:",
                texto_consideracoes,
            ],
            cor=COR_SUCESSO,
        )

        await asyncio.sleep(1)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="🔒 Processando Finalização",
            linhas=[
                "Este canal está passando por algumas etapas de segurança como:",
                "Compressão de imagens/vídeos.",
                "Após essas validações o mesmo será deletado e o transcript "
                "gerado com segurança!",
            ],
            cor=COR_INFO,
        )

        ticket_final = await finalizar_ticket(
            ticket,
            membro,
            consideracoes=texto_consideracoes,
        )
        mensagens = await coletar_mensagens_do_canal(canal)
        html_transcript = montar_html_transcript(
            ticket_final, mensagens, interacao.guild
        )

        if interacao.guild is not None:
            await apagar_call_do_ticket(interacao.guild, ticket_final)

        autor_mention = f"<@{ticket_final.autor_discord_id}>"
        await enviar_log_ticket_finalizado(
            bot=interacao.client,
            ticket=ticket_final,
            staff=membro,
            autor_mention=autor_mention,
            nome_canal=nome_canal_atual,
            consideracoes=texto_consideracoes,
        )

        try:
            autor_user = await interacao.client.fetch_user(
                ticket_final.autor_discord_id
            )
            await enviar_dm_card(
                destino=autor_user,
                titulo="🎫 Ticket Finalizado",
                linhas=[
                    f"Seu ticket **#{ticket_final.id}** "
                    f"({ticket_final.categoria_rotulo}) foi finalizado.",
                    f"Staff: {username_staff}",
                    f"Considerações: {texto_consideracoes}",
                    f"Senha do transcript: ||{ticket_final.senha_transcript}||",
                    "O link de visualização será liberado na próxima fase.",
                ],
                cor=COR_DM_INFO,
            )
        except Exception:
            pass

        _ = html_transcript

        await interacao.followup.send(
            content="Ticket finalizado. O canal será apagado em instantes.",
            ephemeral=True,
        )

        await asyncio.sleep(5)
        try:
            await canal.delete(
                reason=f"Ticket #{ticket_final.id} finalizado por {membro}"
            )
        except discord.HTTPException:
            pass
