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
    listar_membros_com_acesso_extra,
    marcar_ticket_saudado,
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
from src.tickets.tickets_transcript_api import enviar_transcript_para_api
from src.utils.formatacao import para_horario_brasilia
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
    enviar_dm_view,
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
        saudado: bool = False,
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
        if saudado:
            linha_atendimento.add_item(
                discord.ui.Button(
                    label="Já saudado",
                    emoji="👋",
                    style=discord.ButtonStyle.secondary,
                    custom_id="ticket:saudar",
                    disabled=True,
                )
            )
        else:
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
        saudado=bool(getattr(ticket, "saudado", False)),
    )
    try:
        await mensagem.edit(view=view)
    except discord.HTTPException:
        pass


class ViewSelecionarMembro(discord.ui.LayoutView):
    """Ephemeral: seleção de membro (UserSelect / lista / busca por ID)."""

    def __init__(
        self,
        acao: str,
        canal_id: int,
        ticket_id: int,
        autor_discord_id: int,
        opcoes_remover: list[discord.SelectOption] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.acao = acao
        self.canal_id = canal_id
        self.ticket_id = ticket_id
        self.autor_discord_id = autor_discord_id

        titulos = {
            "adicionar": (
                "# 👥 Adicionar Membro ao Ticket\n"
                "> Utilize o menu abaixo para selecionar o membro que fará "
                "parte deste atendimento."
            ),
            "remover": (
                "# ➖ Remover Membro do Ticket\n"
                "> Selecione um membro que foi adicionado a este atendimento."
            ),
            "chamar": (
                "# 📨 Chamar Membro\n"
                "> ⚠️ *O membro será notificado via mensagem direta. "
                "Caso as DMs estejam fechadas, um aviso será exibido.*"
            ),
            "transferir": (
                "# 🔄 Transferir Atendimento\n"
                "> Selecione o staff que vai assumir este ticket."
            ),
        }
        texto = titulos.get(acao, "Selecione um membro")

        componentes: list = [discord.ui.TextDisplay(texto)]

        if acao == "remover":
            if not opcoes_remover:
                componentes.append(
                    discord.ui.TextDisplay(
                        "Não há membros extras com acesso a este ticket."
                    )
                )
            else:
                seletor = discord.ui.Select(
                    placeholder="Membros adicionados…",
                    min_values=1,
                    max_values=1,
                    options=opcoes_remover[:25],
                )
                seletor.callback = self._ao_selecionar
                linha = discord.ui.ActionRow()
                linha.add_item(seletor)
                componentes.append(linha)
        else:
            seletor = discord.ui.UserSelect(
                placeholder="Escolha um membro…",
                min_values=1,
                max_values=1,
            )
            seletor.callback = self._ao_selecionar
            linha = discord.ui.ActionRow()
            linha.add_item(seletor)
            componentes.append(linha)

            if acao in ("adicionar", "chamar"):
                linha_id = discord.ui.ActionRow()
                botao_id = discord.ui.Button(
                    label="Buscar por Discord ID",
                    emoji="🔍",
                    style=discord.ButtonStyle.secondary,
                )
                botao_id.callback = self._abrir_modal_id
                linha_id.add_item(botao_id)
                componentes.append(linha_id)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)

    async def _abrir_modal_id(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_modal(
            ModalBuscarDiscordId(
                acao=self.acao,
                canal_id=self.canal_id,
                ticket_id=self.ticket_id,
                autor_discord_id=self.autor_discord_id,
            )
        )

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Nenhum membro foi selecionado."],
            )
            return
        await _executar_acao_membro(
            interacao=interacao,
            acao=self.acao,
            membro_id=int(valores[0]),
            canal_id=self.canal_id,
            autor_discord_id=self.autor_discord_id,
        )


class ModalBuscarDiscordId(discord.ui.Modal, title="Buscar por Discord ID"):
    """Modal para informar o ID numérico do membro."""

    discord_id = discord.ui.TextInput(
        label="Discord ID",
        style=discord.TextStyle.short,
        placeholder="Ex.: 859100649366356000",
        required=True,
        max_length=25,
    )

    def __init__(
        self,
        acao: str,
        canal_id: int,
        ticket_id: int,
        autor_discord_id: int,
    ) -> None:
        super().__init__()
        self.acao = acao
        self.canal_id = canal_id
        self.ticket_id = ticket_id
        self.autor_discord_id = autor_discord_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        texto = str(self.discord_id.value).strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números do Discord ID."],
            )
            return
        await _executar_acao_membro(
            interacao=interacao,
            acao=self.acao,
            membro_id=int(texto),
            canal_id=self.canal_id,
            autor_discord_id=self.autor_discord_id,
        )


async def _executar_acao_membro(
    interacao: discord.Interaction,
    acao: str,
    membro_id: int,
    canal_id: int,
    autor_discord_id: int,
) -> None:
    """Executa adicionar / remover / chamar / transferir após escolher o membro."""
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

    canal = guilda.get_channel(canal_id)
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

    if acao == "adicionar":
        await adicionar_membro_ao_ticket(canal, membro_alvo)
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="➕ Usuário Adicionado ao Ticket",
            linhas=[
                f"{membro_alvo.mention} foi adicionado ao ticket por {staff.mention}",
            ],
            cor=COR_SUCESSO,
        )
        return

    if acao == "remover":
        erro = await remover_membro_do_ticket(
            canal,
            membro_alvo,
            autor_discord_id,
        )
        if erro:
            await responder_erro(
                interacao,
                titulo="Não foi possível remover",
                linhas=[erro],
            )
            return
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="➖ Usuário Removido do Ticket",
            linhas=[
                f"{membro_alvo.mention} foi removido do ticket por {staff.mention}.",
            ],
            cor=COR_INFO,
        )
        return

    if acao == "chamar":
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)

        link_canal = canal.jump_url
        nome_ticket = canal.name
        username_alvo = nome_usuario_discord(membro_alvo)

        enviou = await enviar_dm_card(
            destino=membro_alvo,
            titulo="📨 Membro Chamado",
            linhas=[
                "> Você está sendo chamado no ticket, clique abaixo para "
                "retomar o atendimento.",
                f"**👤 Membro:** `{username_alvo}`",
                f"**📋 Ticket:** [ `{nome_ticket}` ]",
                "**💬 Mensagem:**",
                "> Por favor, compareça ao ticket acima para tratarmos "
                "de um assunto importante.",
            ],
            cor=COR_DM_INFO,
            botoes_link=[("Abrir ticket", link_canal)],
            guilda=guilda,
            registrar_log=False,
        )

        if enviou:
            await enviar_card_no_canal_ticket(
                canal,
                titulo="📨 Membro Chamado",
                linhas=[
                    f"{membro_alvo.mention} foi notificado por DM por {staff.mention}.",
                ],
                cor=COR_SUCESSO,
            )
        else:
            await enviar_card_no_canal_ticket(
                canal,
                titulo="🔒 DMs Bloqueadas",
                linhas=[
                    "> ⚠️ **Não foi possível notificar este membro.**",
                    f"**👤 Membro:** {membro_alvo.mention}",
                    "**❌ Motivo:** As mensagens diretas deste membro "
                    "estão **fechadas/bloqueadas**.",
                    "### 💡 O que fazer?",
                    "- Tente contatá-lo por outro meio disponível",
                    "- Solicite que ele habilite as DMs temporariamente",
                    "- Utilize um canal alternativo de comunicação do servidor",
                    "🔁 *Tente novamente após a liberação das DMs.*",
                ],
                cor=COR_INFO,
            )
        return

    if acao == "transferir":
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

        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)
        ticket_atualizado, _nome = await transferir_atendimento(
            ticket, membro_alvo, canal
        )
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
        membros_extra = listar_membros_com_acesso_extra(canal, ticket.autor_discord_id)
        opcoes = [
            discord.SelectOption(
                label=nome_usuario_discord(m)[:100],
                value=str(m.id),
                description=f"ID {m.id}"[:100],
            )
            for m in membros_extra[:25]
        ]
        await responder_view(
            interacao,
            view=ViewSelecionarMembro(
                acao="remover",
                canal_id=canal.id,
                ticket_id=ticket.id,
                autor_discord_id=ticket.autor_discord_id,
                opcoes_remover=opcoes,
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
            delay=10,
        )
        return

    await interacao.response.defer(ephemeral=True)
    ticket_atualizado, nome_aplicado = await assumir_ticket(ticket, membro, canal)
    await atualizar_card_botoes_staff(canal, ticket_atualizado)

    await enviar_card_no_canal_ticket(
        canal,
        titulo="🎫 Atendimento Assumido",
        linhas=[
            f"Assumido por {membro.mention} canal alterado para o novo nome: "
            f"`{nome_aplicado}`",
        ],
        cor=COR_SUCESSO,
    )
    # Confirmação ephemeral curta (some sozinha)
    await responder_card(
        interacao,
        titulo="🎫 Atendimento Assumido",
        linhas=[f"Este atendimento foi assumido por {membro.mention}."],
        cor=COR_SUCESSO,
        delay=10,
    )


async def _tratar_saudar(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    if getattr(ticket, "saudado", False):
        await responder_card(
            interacao,
            titulo="Já saudado",
            linhas=["A saudação inicial já foi enviada neste ticket."],
            cor=COR_INFO,
            delay=10,
        )
        return

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
    ticket_atualizado = await marcar_ticket_saudado(ticket.id)
    if ticket_atualizado is not None:
        await atualizar_card_botoes_staff(canal, ticket_atualizado)


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


def montar_dm_ticket_finalizado(
    ticket,
    username_staff: str,
    consideracoes: str,
    guilda: discord.Guild | None,
) -> discord.ui.LayoutView:
    """
    Card de DM para o autor quando o ticket é finalizado.

    Inclui thumbnail do ícone do servidor e botões:
    Acessar Transcript | Senha (desativado) | Avaliar Atendimento.
    """
    from src.config import (
        CANAIS,
        GUILD_ID,
        TICKETS_CATEGORIAS,
    )
    from src.tickets.tickets_logger import _montar_linha_botoes_transcript

    data_abertura = para_horario_brasilia(ticket.aberto_em)
    if data_abertura is not None:
        texto_abertura = data_abertura.strftime("%d/%m/%Y às %H:%M:%S")
    else:
        texto_abertura = "—"

    definicao = TICKETS_CATEGORIAS.get(ticket.categoria_chave) or {}
    emoji_categoria = definicao.get("emoji") or ""
    rotulo = ticket.categoria_rotulo
    senha = ticket.senha_transcript or "—"

    texto_corpo = (
        f"Seu Ticket de ID: [`{ticket.id}`]"
        f"\n\n"
        f"Categoria: `{emoji_categoria} {rotulo}`"
        f"\n\n"
        f"Que foi aberto dia **{texto_abertura}**"
        f"\n\n"
        f"**Acabou de ser Finalizado!**"
        f"\n\n"
        f"Responsável por Finalizar: `{username_staff}`"
        f"\n\n"
        f"> **✏️ __Considerações Finais:__**"
        f"\n"
        f"# - {consideracoes}"
        f"\n\n"
        f"> **🔐 __Senha para visualização do Transcript:__**"
        f"\n"
        f"# ||**`{senha}`**||"
    )

    componentes: list = []
    url_icone = None
    if guilda is not None and guilda.icon is not None:
        url_icone = guilda.icon.url

    titulo = "# Ticket Finalizado! 📋"
    if url_icone:
        componentes.append(
            discord.ui.Section(
                f"{titulo}\n\n{texto_corpo}",
                accessory=discord.ui.Thumbnail(url_icone),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(f"{titulo}\n\n{texto_corpo}"))
    # Botões: transcript + senha + avaliar
    linha = _montar_linha_botoes_transcript(ticket)

    canal_avaliar_id = CANAIS.get("CANAL_AVALIAR_ATENDIMENTO") or 0
    guild_id = int(GUILD_ID)
    if guilda is not None:
        guild_id = guilda.id
    if canal_avaliar_id:
        url_avaliar = f"https://discord.com/channels/{guild_id}/{int(canal_avaliar_id)}"
        linha.add_item(
            discord.ui.Button(
                label="Avaliar Atendimento",
                emoji="⭐",
                style=discord.ButtonStyle.link,
                url=url_avaliar,
            )
        )
    else:
        linha.add_item(
            discord.ui.Button(
                label="Avaliar Atendimento",
                emoji="⭐",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(linha)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.green())
    )
    return view


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
            texto_consideracoes = "**Atendimento Finalizado**"

        await interacao.response.defer(ephemeral=True)

        nome_canal_atual = canal.name
        username_staff = nome_usuario_discord(membro)

        await enviar_card_no_canal_ticket(
            canal,
            titulo="🎫 Ticket Finalizado com Sucesso",
            linhas=[
                f"**Responsável pela finalização:** {membro.mention} (`{username_staff}`)\n",
                "## 📝 Considerações Finais",
                f"- {texto_consideracoes}",
                "\n*O ticket foi encerrado e será processado para arquivamento.*",
            ],
            cor=COR_SUCESSO,
        )

        await asyncio.sleep(1)
        await enviar_card_no_canal_ticket(
            canal,
            titulo="🔄 Processando Finalização",
            linhas=[
                "### ⚙️ Etapas de Segurança em Andamento\n",
                "📦 **Compactação de Mídia**",
                "- Comprimindo imagens e vídeos...\n",
                "🔒 **Validação de Dados**",
                "- Verificando integridade dos arquivos anexados\n",
                "- Removendo metadados sensíveis\n",
                "## 📄 Geração do Transcript",
                "*Após a conclusão das etapas acima, o transcript será gerado com segurança e o canal será deletado automaticamente.*\n",
                "⏳ Aguarde enquanto processamos as últimas etapas...",
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

        # Obrigatório: publicar na API antes do log/DM (habilita botão Acessar)
        print(
            f"📤 [transcript] iniciando upload ticket=#{ticket_final.id} "
            f"html_chars={len(html_transcript or '')}"
        )
        url_publica = await enviar_transcript_para_api(
            ticket_final,
            html_transcript,
        )
        if url_publica:
            ticket_final.url_transcript = url_publica
            print(f"✅ [transcript] url gravada: {url_publica}")
        else:
            print(
                f"⚠️ [transcript] upload falhou no ticket #{ticket_final.id} "
                "— botão Acessar permanecerá desativado"
            )
            try:
                await interacao.followup.send(
                    content=(
                        "⚠️ Transcript **não** foi publicado na API. "
                        "Confira `BACKUP_API_TOKEN` e "
                        "`CMSVALLEY_API_URL` no ambiente do bot."
                    ),
                    ephemeral=True,
                )
            except Exception:
                pass

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
            view_dm = montar_dm_ticket_finalizado(
                ticket=ticket_final,
                username_staff=username_staff,
                consideracoes=texto_consideracoes,
                guilda=interacao.guild,
            )
            await enviar_dm_view(
                destino=autor_user,
                view=view_dm,
                titulo_log="Ticket Finalizado (DM autor)",
                linhas_resumo=[
                    f"Ticket #{ticket_final.id}",
                    ticket_final.categoria_rotulo,
                ],
                guilda=interacao.guild,
                registrar_log=True,
            )
        except Exception as erro_dm:
            print(f"⚠️ [transcript] falha ao enviar DM do autor: {erro_dm}")

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
